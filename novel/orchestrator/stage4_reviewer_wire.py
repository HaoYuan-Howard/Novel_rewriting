#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 A/B Reviewer 的原生裁决载体与可信组装器。

模型只填写当前 option 开放的决定、理由与 typed 约束。章号变为固定 object
键，actor、review token、review run 与运输 wrapper 由 immutable exact_request
注入；多选工作包的 stop 始终保留。这里不另造审批判据，也不把 provider schema
当作批准：完整结果仍必须经过 ``reviewer_tasks.load_action`` 与正式 producer。

动态字段直接投影现役 Pydantic model；章级引用只使用同一 workpack 从 typed
subject 投影的闭集。约束数组的集合式排序仍由现役 loader 负责，引用数组的
业务顺序原样保留。本模块不读产物、不修改业务 token、不返还旧席位预算。
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal, Protocol, get_args

from pydantic import Field, ValidationError, create_model
from pydantic_core import PydanticUndefined
from typing_extensions import TypeAliasType

from contracts.invariants import DESIGN_REPAIR_CODE_INSTRUCTIONS, commander_review_actions
from contracts.schema.api import (
    DesignConstraintCatalog,
    DesignConstraintOperation,
    DesignConstraintOperations,
    DesignChapterRuling,
    DesignRepairConstraint,
    DesignRepairObserves,
    DesignRepairResponseTo,
    DesignRepairSameScene,
    DesignRepairStoryBefore,
    DesignReviewRequest,
    DesignStrategyReplanRuling,
    DesignStrategyRuling,
    StructureApprovalRequest,
    StructureChapterDecision,
)
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from novel.agents.session_client import agent_json_schema
from novel.orchestrator import queue


WIRE_SCHEMA = 'novel-stage4-reviewer-rulings-v1'
REFERENCE_CLOSURE = 'chapters[].constraints[].reference_closure'
CONSTRAINT_OPERATION_CATALOG = 'chapters[].constraint_operations.active'
_DESIGN_BLOCKERS = frozenset({
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
})
_BLOCKERS = _DESIGN_BLOCKERS | {queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW}
_EFFECT_MODELS = (
    DesignRepairStoryBefore, DesignRepairResponseTo,
    DesignRepairSameScene, DesignRepairObserves,
)


class Stage4ReviewerWireError(RuntimeError):
    """当前 immutable option 无法唯一、无损地解释响应。"""


class _ReviewerTaskLike(Protocol):
    waiting_on: str
    submission_options: Sequence[Mapping[str, Any]]


def _sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()


def uses_compact_wire(task: object) -> bool:
    """已登记的 Stage4 blocker 必须走本协议；坏模板不得静默退回旧协议。"""
    return getattr(task, 'waiting_on', None) in _BLOCKERS


def _options(task: _ReviewerTaskLike) -> list[Mapping[str, Any]]:
    if not uses_compact_wire(task):
        raise Stage4ReviewerWireError('当前 task 不是 Stage4 Reviewer blocker')
    options = list(task.submission_options)
    expected_surface = 'design' if task.waiting_on in _DESIGN_BLOCKERS else 'structure'
    seen: set[str] = set()
    terminal_kinds: set[str] = set()
    allowed_terminals = set(commander_review_actions(task.waiting_on)) & {'uncertain', 'stop'}
    for option in options:
        exact = option.get('exact_request') if isinstance(option, Mapping) else None
        action = exact.get('action') if isinstance(exact, Mapping) else None
        option_id = option.get('id') if isinstance(option, Mapping) else None
        if (not isinstance(option_id, str) or not option_id or option_id in seen
                or not isinstance(exact, dict)
                or exact.get('schema') != 'novel-reviewer-action-v1'
                or exact.get('waiting_on') != task.waiting_on
                or not isinstance(action, Mapping)):
            raise Stage4ReviewerWireError('Stage4 option 身份重复、缺失或绑定漂移')
        seen.add(option_id)
        if action.get('kind') in ('stop', 'uncertain'):
            kind = action['kind']
            if kind not in allowed_terminals or kind in terminal_kinds:
                raise Stage4ReviewerWireError('当前 Stage4 blocker 未登记此停止动作或动作重复')
            terminal_kinds.add(kind)
        elif (action.get('kind') != 'approval'
                or action.get('surface') != expected_surface):
            raise Stage4ReviewerWireError('Stage4 option 超出当前审批面')
    if ('approval:' + expected_surface not in seen
            or terminal_kinds != allowed_terminals
            or len(seen) != 1 + len(allowed_terminals)):
        raise Stage4ReviewerWireError('Stage4 option 必须完整保留当前合法选择')
    return options


def _literal(values: Sequence[Any]) -> Any:
    if not values or len(values) != len(set(values)):
        raise Stage4ReviewerWireError('Stage4 动态枚举为空或重复')
    return Literal[tuple(values)]


def _field(model: type[StrictModel], name: str, annotation: Any = None) -> tuple[Any, Any]:
    """只投影生产字段；wire 每个开放字段必须显式交回。"""
    field = copy.deepcopy(model.model_fields[name])
    field.default = PydanticUndefined
    field.default_factory = None
    return (field.annotation if annotation is None else annotation, field)


def _closed_enum(option: Mapping[str, Any], key: str, model: type[StrictModel], name: str) -> Any:
    allowed = option.get('allowed_values')
    values = allowed.get(key) if isinstance(allowed, Mapping) else None
    current = get_args(model.model_fields[name].annotation)
    if (not isinstance(values, list) or not values
            or any(value not in current for value in values)):
        raise Stage4ReviewerWireError('Stage4 option 枚举未继承现役 typed 字段')
    return _literal(values)


def _chapter_templates(exact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    payload = exact['action']['payload']
    if not isinstance(payload, Mapping):
        raise Stage4ReviewerWireError('Stage4 可信 payload 不是 object')
    rows = payload.get('chapters')
    if (not isinstance(rows, list) or not rows
            or any(not isinstance(row, Mapping) for row in rows)):
        raise Stage4ReviewerWireError('Stage4 可信模板缺全书章节分母')
    ordinals = [row.get('output_ordinal') for row in rows]
    if (any(type(ordinal) is not int or ordinal < 1 for ordinal in ordinals)
            or ordinals != sorted(set(ordinals))):
        raise Stage4ReviewerWireError('Stage4 可信章号必须是升序不重复正整数')
    return rows


def _constraint_model(option: Mapping[str, Any], ordinal: int, digest: str) -> type[StrictModel]:
    allowed = option.get('allowed_values')
    closures = allowed.get(REFERENCE_CLOSURE) if isinstance(allowed, Mapping) else None
    closure = closures.get(str(ordinal)) if isinstance(closures, Mapping) else None
    if not isinstance(closure, Mapping):
        raise Stage4ReviewerWireError('Stage4 章级约束缺当前 subject 引用闭集')
    for key in ('scene_refs', 'fact_aliases', 'participant_aliases'):
        values = closure.get(key)
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))):
            raise Stage4ReviewerWireError('Stage4 subject 引用闭集缺失或重复')
    # 同一章的多个 relation 端点复用 $defs；不要把完整 alias 表逐端点展开，
    # 否则长书会因运输 schema 膨胀再次触发 provider 协议拒绝。
    scenes = TypeAliasType(
        f'Stage4SceneRef_{ordinal}_{digest}', _literal(closure['scene_refs']))
    facts = TypeAliasType(
        f'Stage4FactAlias_{ordinal}_{digest}', _literal(closure['fact_aliases']))
    participants = (TypeAliasType(
        f'Stage4ParticipantAlias_{ordinal}_{digest}',
        _literal(closure['participant_aliases']))
        if closure['participant_aliases'] else None)
    aliases = TypeAliasType(
        f'Stage4OpaqueAlias_{ordinal}_{digest}',
        facts if participants is None else facts | participants)
    effects = []
    for model in _EFFECT_MODELS:
        if model is DesignRepairObserves and participants is None:
            # 真相章没有 participant 时不存在可填写的 observer，不能伪造占位值。
            continue
        # 当前 producer 在整条 truth relation 上检查行动权；这里只冻结端点域，
        # 不从自由文字重新解释允许哪些关系，更不替 producer 批准关系。
        effects.append(create_model(
            f'Stage4{model.__name__}_{ordinal}_{digest}', __base__=model,
            **{name: _field(
                model, name, participants if name == 'observer_alias' else facts)
               for name in model.model_fields if name.endswith('_alias')},
        ))
    return create_model(
        f'Stage4ReviewerConstraint_{ordinal}_{digest}', __base__=DesignRepairConstraint,
        code=_field(DesignRepairConstraint, 'code', _closed_enum(
            option, 'chapters[].constraints[].code', DesignRepairConstraint, 'code')),
        scene_refs=_field(DesignRepairConstraint, 'scene_refs', list[scenes]),
        opaque_aliases=_field(DesignRepairConstraint, 'opaque_aliases', list[aliases]),
        effect=_field(DesignRepairConstraint, 'effect', reduce(or_, effects) | None),
    )


def _constraint_operation_scopes(
    option: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """目录缺失不是零条义务；新 task 必须显式绑定全章 active 集。"""
    allowed = option.get('allowed_values')
    catalog = allowed.get(CONSTRAINT_OPERATION_CATALOG) if isinstance(allowed, Mapping) else None
    if not isinstance(catalog, Mapping) or set(catalog) != {
            str(row['output_ordinal']) for row in rows}:
        raise Stage4ReviewerWireError('Stage4 历史约束目录必须完整绑定当前章集')
    for scope in catalog.values():
        if not isinstance(scope, Mapping):
            raise Stage4ReviewerWireError('Stage4 历史约束目录不是 object')
        items = scope.get('items')
        if (not isinstance(items, list)
                or any(not isinstance(item, Mapping) for item in items)):
            raise Stage4ReviewerWireError('Stage4 历史约束目录缺显式 items 分母')
        try:
            parsed = DesignConstraintCatalog.model_validate(scope)
        except ValidationError as exc:
            raise Stage4ReviewerWireError('Stage4 历史约束目录未继承现役 typed 形状') from exc
        identities = [item.constraint_sha256 for item in parsed.items]
        if identities != sorted(set(identities)):
            raise Stage4ReviewerWireError('Stage4 历史约束目录身份必须升序不重复')
    return dict(catalog)


def _constraint_operations_model(
    scope: Mapping[str, Any], constraint: type[StrictModel], ordinal: int, digest: str,
) -> type[StrictModel]:
    """把 active SHA 提升成固定键；canonical 身份与集合 SHA 由组装器注入。"""
    choices = []
    for kind in get_args(DesignConstraintOperation.model_fields['kind'].annotation):
        # canonical 的 kind/replacements 条件在原生 schema 中也收窄为局部分支。
        # 组装后仍复用同一 canonical model，不让运输层成为第二个审批判官。
        replacements = Annotated[list[constraint], (
            Field(min_length=1) if kind == 'supersede' else Field(max_length=0))]
        choices.append(create_model(
            f'Stage4ConstraintOperation_{kind}_{ordinal}_{digest}', __base__=StrictModel,
            kind=_field(DesignConstraintOperation, 'kind', Literal[kind]),
            reason=_field(DesignConstraintOperation, 'reason'),
            replacements=_field(DesignConstraintOperation, 'replacements', replacements),
        ))
    return create_model(
        f'Stage4ConstraintOperations_{ordinal}_{digest}', __base__=StrictModel,
        **{item['constraint_sha256']: (reduce(or_, choices), ...)
           for item in scope['items']},
    )


def _payload_model(option: Mapping[str, Any], digest: str) -> type[StrictModel]:
    exact = option['exact_request']
    action = exact['action']
    if action['kind'] in ('stop', 'uncertain'):
        return create_model(
            f'Stage4ReviewerStop_{digest}', __base__=StrictModel,
            reason=(str, Field(..., min_length=1)),
        )
    rows = _chapter_templates(exact)
    design = action['surface'] == 'design'
    chapter_source = DesignChapterRuling if design else StructureChapterDecision
    chapter_fields = {}
    if design:
        operation_scopes = _constraint_operation_scopes(option, rows)
        closure = option.get('allowed_values', {}).get(REFERENCE_CLOSURE)
        if not isinstance(closure, Mapping) or set(closure) != {
                str(row['output_ordinal']) for row in rows}:
            raise Stage4ReviewerWireError('Stage4 引用闭集必须完整绑定当前章集')
    for row in rows:
        if row.get('source_decision_sha256'):
            # 继承是后端已冻结的凭据，不是本轮模型的开放裁决项。
            continue
        ordinal = row['output_ordinal']
        fields = {
            'decision': _field(chapter_source, 'decision', _closed_enum(
                option, 'chapters[].decision', chapter_source, 'decision')),
            'reason': _field(chapter_source, 'reason'),
        }
        if design:
            constraint = _constraint_model(option, ordinal, digest)
            fields['constraints'] = _field(
                DesignChapterRuling, 'constraints', list[constraint])
            operation_scope = operation_scopes[str(ordinal)]
            if operation_scope['items']:
                fields['constraint_operations'] = (
                    _constraint_operations_model(
                        operation_scope, constraint, ordinal, digest), ...)
        chapter_fields[str(ordinal)] = (create_model(
            f'Stage4ReviewerChapter_{ordinal}_{digest}',
            __base__=StrictModel, **fields,
        ), ...)
    chapters = create_model(
        f'Stage4ReviewerChapters_{digest}', __base__=StrictModel,
        **chapter_fields,
    )
    if not design:
        return create_model(
            f'Stage4ReviewerStructure_{digest}', __base__=StrictModel,
            reason=_field(StructureApprovalRequest, 'reason'), chapters=(chapters, ...),
        )
    if exact['action']['payload']['strategy'].get('source_decision_sha256'):
        return create_model(
            f'Stage4ReviewerDesign_{digest}', __base__=StrictModel,
            general_reason=_field(DesignReviewRequest, 'general_reason'),
            chapters=(chapters, ...),
        )
    replan = create_model(
        f'Stage4ReviewerReplan_{digest}', __base__=StrictModel,
        kind=_field(DesignStrategyReplanRuling, 'kind', _closed_enum(
            option, 'strategy.replan_constraints[].kind', DesignStrategyReplanRuling, 'kind')),
        chapter_ordinals=_field(DesignStrategyReplanRuling, 'chapter_ordinals',
                                list[_literal([row['output_ordinal'] for row in rows])]),
    )
    strategy = create_model(
        f'Stage4ReviewerStrategy_{digest}', __base__=StrictModel,
        decision=_field(DesignStrategyRuling, 'decision', _closed_enum(
            option, 'strategy.decision', DesignStrategyRuling, 'decision')),
        reason=_field(DesignStrategyRuling, 'reason'),
        replan_constraints=_field(DesignStrategyRuling, 'replan_constraints', list[replan]),
    )
    return create_model(
        f'Stage4ReviewerDesign_{digest}', __base__=StrictModel,
        general_reason=_field(DesignReviewRequest, 'general_reason'),
        strategy=(strategy, ...), chapters=(chapters, ...),
    )


def response_model(task: _ReviewerTaskLike) -> type[StrictModel]:
    """由完整 frozen options 构造本轮专有 model，不静默删除 stop。"""
    choices = []
    for option in _options(task):
        # 名称只绑定响应角色；候选/token 的变化不是 schema 结构变化。
        digest = _sha256(option['id'])[:16]
        choices.append(create_model(
            f'Stage4ReviewerChoice_{digest}', __base__=StrictModel,
            option_id=(_literal([option['id']]), ...),
            expected_exact_request_sha256=(Sha256, ...),
            payload=(_payload_model(option, digest), ...),
        ))
    return create_model(
        f'Stage4ReviewerEnvelope_{_sha256([option["id"] for option in _options(task)])[:16]}',
        __base__=StrictModel,
        schema_=(Literal[WIRE_SCHEMA], Field(..., alias='schema')),
        selection=(reduce(or_, choices), ...),
    )


def output_schema_for(task: object) -> dict[str, Any] | None:
    if not uses_compact_wire(task):
        return None
    return agent_json_schema(response_model(task))  # type: ignore[arg-type]


def prompt_for(task: object, base_prompt: str) -> str:
    if not uses_compact_wire(task):
        return base_prompt
    choices = [{
        'option_id': option['id'],
        'expected_exact_request_sha256': _sha256(option['exact_request']),
    } for option in _options(task)]  # type: ignore[arg-type]
    directions = json.dumps({code: DESIGN_REPAIR_CODE_INSTRUCTIONS[code]
                             for code in ('scene_goal', 'scene_goal_exclude')},
                            ensure_ascii=False, separators=(',', ':'))
    return f'''{base_prompt.rstrip()}

【Stage4 Reviewer 原生裁决输出】
本轮使用 CLI 原生 JSON Schema；上文复制完整 exact_request 的运输要求由本段替换。
最终只输出 {WIRE_SCHEMA} 裸 JSON object，结构为
{{"schema":"{WIRE_SCHEMA}","selection":{{"option_id":...,"expected_exact_request_sha256":...,"payload":...}}}}。
selection 必须完整选择一个合法 option，不得把 id 写到 JSON 末尾或复制完整 action。
当前选项与绑定摘要：{json.dumps(choices, ensure_ascii=False, separators=(',', ':'))}

design payload 交 general_reason 和 chapters；只有策略待审时交 strategy，其中交
decision/reason/replan_constraints，chapters 是按 output_ordinal 的十进制字符串
固定键组成的 object，每章交 decision/reason/constraints；原生 schema 开放了
constraint_operations 的章还必须逐项填写该 object。structure payload 只交
reason 和同样固定键的 chapters，每章交 decision/reason。stop payload 只交 reason。
原生 schema 只开放待审章节键；带 source_decision_sha256 的历史接受由组装器完整继承，
不要回抄或改写其章节及策略。开放的全部键和字段必须出现；待审章为空交 chapters: {{}}。
空约束交 []，无 relation effect 交 null。不要回抄
actor、reviewer_kind、review_run_id、review_token、output_ordinal 或 surface_seal；
Supervisor 只从该 immutable option 注入这些身份，随后仍走现役 typed loader 和审批。
未审模板里的占位决定不是历史裁决，空 constraints 也不表示已无历史义务；
真实历史只认工作包的 design_constraint_catalog。constraints 只添加本次新约束。
对 active 非空的章，constraint_operations 按原生 schema 的每个固定 SHA 键逐项交
kind/reason/replacements：keep 与 withdraw 必须交 replacements: []，supersede 必须
交非空 typed replacements。全键必填，不能遗漏、重复、添加其他章身份或交 null。
Supervisor 从同一目录注入 expected_constraint_set_sha256 和 canonical items 的
constraint_sha256；模型不回抄这些绑定字段。无 active 约束的章不添加该 object。
撤销和替代必须是你针对真实历史义务作出的明确决定，不能把修后主体变化当作自动关闭。
场景目标方向使用已登记语义：{directions}
选择的 code 必须表达你要 Executor 执行的方向；reason 只作审计，不能反转 code 的含义。

typed 约束使用原生 schema 的当前章 scene/alias 闭集；引用数组顺序表达真实修订意图，
不得拿空数组或理由代替实际决定。INFO/metric/UNVER 的阅读权与修订行动权保持原契约。
'''


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Stage4ReviewerWireError('Stage4 wire JSON object 包含重复键')
        result[key] = value
    return result


def assemble(task: _ReviewerTaskLike, response_document: Mapping[str, Any] | str) -> dict[str, Any]:
    """只组装运输形状；调用方必须继续交给 ``load_action`` 完成现役校验。"""
    model = response_model(task)
    try:
        raw = (json.loads(response_document, object_pairs_hook=_unique_object)
               if isinstance(response_document, str) else response_document)
        if not isinstance(raw, Mapping):
            raise Stage4ReviewerWireError('Stage4 wire 响应必须是 JSON object')
        if set(raw) != {'schema', 'selection'}:
            raise Stage4ReviewerWireError('Stage4 wire 顶层字段必须是 schema/selection')
        parsed = model.model_validate(dict(raw), strict=True).model_dump(mode='json', by_alias=True)
    except ValidationError as exc:
        # 不让不可信 extra-key、理由或 constraint 值进入公开拒因/成本账。
        kinds = sorted({row['type'] for row in exc.errors(include_input=False)})
        raise Stage4ReviewerWireError(
            f'Stage4 wire 响应不符合当前动态 schema：{",".join(kinds)}') from exc
    except (ValueError, TypeError) as exc:
        # Python 的超长整数限制也会抛普通 ValueError，仍属于可订正的响应拒因。
        raise Stage4ReviewerWireError('Stage4 wire 响应不是唯一完整 JSON object') from exc
    selected = parsed['selection']
    option = next(option for option in _options(task) if option['id'] == selected['option_id'])
    if selected['expected_exact_request_sha256'] != _sha256(option['exact_request']):
        raise Stage4ReviewerWireError('Stage4 wire exact-request SHA 已 stale')
    assembled = copy.deepcopy(option['exact_request'])
    action = assembled['action']
    supplied = selected['payload']
    if action['kind'] in ('stop', 'uncertain'):
        action['reason'] = supplied['reason']
        return assembled
    payload = action['payload']
    chapter_map = supplied.pop('chapters')
    for field, value in supplied.items():
        payload[field] = value
    operation_scopes = (
        _constraint_operation_scopes(option, payload['chapters'])
        if action['surface'] == 'design' else {})
    for row in payload['chapters']:
        if row.get('source_decision_sha256'):
            continue
        ordinal = str(row['output_ordinal'])
        ruling = chapter_map[ordinal]
        if action['surface'] == 'design':
            # 待审项的模型判断全部取本次回答；不能继承未审脚手架。
            row.pop('constraint_operations', None)
            operations = ruling.pop('constraint_operations', None)
            if operations is not None:
                scope = operation_scopes[ordinal]
                items = []
                for item in scope['items']:
                    operation = dict(operations[item['constraint_sha256']],
                                     constraint_sha256=item['constraint_sha256'])
                    # 约束集合可规范排序，内部 refs 的业务顺序原样保留；重复仍交
                    # canonical validator 拒绝，不能去重后改写模型的实际回答。
                    operation['replacements'] = sorted(
                        operation['replacements'], key=lambda value: json.dumps(
                            value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
                    items.append(operation)
                try:
                    canonical = DesignConstraintOperations.model_validate({
                        'expected_constraint_set_sha256': scope['expected_constraint_set_sha256'],
                        'items': items,
                    })
                except ValidationError as exc:
                    raise Stage4ReviewerWireError('Stage4 历史约束处置未通过现役 typed 校验') from exc
                ruling['constraint_operations'] = canonical.model_dump(mode='json')
        row.update(ruling)
    return assembled


__all__ = [
    'WIRE_SCHEMA', 'REFERENCE_CLOSURE', 'CONSTRAINT_OPERATION_CATALOG',
    'Stage4ReviewerWireError',
    'uses_compact_wire', 'output_schema_for', 'response_model', 'prompt_for', 'assemble',
]
