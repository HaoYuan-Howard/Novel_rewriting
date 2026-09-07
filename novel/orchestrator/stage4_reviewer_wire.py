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
from typing import Any, Literal, Protocol, get_args

from pydantic import Field, ValidationError, create_model
from pydantic_core import PydanticUndefined
from typing_extensions import TypeAliasType

from contracts.schema.api import (
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
from novel.agents.session_client import agent_json_schema
from novel.orchestrator import queue


WIRE_SCHEMA = 'novel-stage4-reviewer-rulings-v1'
REFERENCE_CLOSURE = 'chapters[].constraints[].reference_closure'
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
        if action.get('kind') == 'stop':
            if task.waiting_on != queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN:
                raise Stage4ReviewerWireError('当前 Stage4 blocker 未登记 stop')
        elif (action.get('kind') != 'approval'
                or action.get('surface') != expected_surface):
            raise Stage4ReviewerWireError('Stage4 option 超出当前审批面')
    expected_ids = {'approval:' + expected_surface}
    if task.waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN:
        expected_ids.add('stop:stage4-structure-replan')
    if seen != expected_ids:
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


def _payload_model(option: Mapping[str, Any], digest: str) -> type[StrictModel]:
    exact = option['exact_request']
    action = exact['action']
    if action['kind'] == 'stop':
        return create_model(
            f'Stage4ReviewerStop_{digest}', __base__=StrictModel,
            reason=(str, Field(..., min_length=1)),
        )
    rows = _chapter_templates(exact)
    design = action['surface'] == 'design'
    chapter_source = DesignChapterRuling if design else StructureChapterDecision
    chapter_fields = {}
    if design:
        closure = option.get('allowed_values', {}).get(REFERENCE_CLOSURE)
        if not isinstance(closure, Mapping) or set(closure) != {
                str(row['output_ordinal']) for row in rows}:
            raise Stage4ReviewerWireError('Stage4 引用闭集必须完整绑定当前章集')
    for row in rows:
        ordinal = row['output_ordinal']
        fields = {
            'decision': _field(chapter_source, 'decision', _closed_enum(
                option, 'chapters[].decision', chapter_source, 'decision')),
            'reason': _field(chapter_source, 'reason'),
        }
        if design:
            fields['constraints'] = _field(
                DesignChapterRuling, 'constraints',
                list[_constraint_model(option, ordinal, digest)])
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
        exact_sha = _sha256(option['exact_request'])
        digest = _sha256(option)[:16]
        choices.append(create_model(
            f'Stage4ReviewerChoice_{digest}', __base__=StrictModel,
            option_id=(_literal([option['id']]), ...),
            expected_exact_request_sha256=(_literal([exact_sha]), ...),
            payload=(_payload_model(option, digest), ...),
        ))
    return create_model(
        f'Stage4ReviewerEnvelope_{_sha256(list(task.submission_options))[:16]}',
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
    return f'''{base_prompt.rstrip()}

【Stage4 Reviewer 原生裁决输出】
本轮使用 CLI 原生 JSON Schema；上文复制完整 exact_request 的运输要求由本段替换。
最终只输出 {WIRE_SCHEMA} 裸 JSON object，结构为
{{"schema":"{WIRE_SCHEMA}","selection":{{"option_id":...,"expected_exact_request_sha256":...,"payload":...}}}}。
selection 必须完整选择一个合法 option，不得把 id 写到 JSON 末尾或复制完整 action。
当前选项与绑定摘要：{json.dumps(choices, ensure_ascii=False, separators=(',', ':'))}

design payload 只交 general_reason、strategy 和 chapters；strategy 交
decision/reason/replan_constraints，chapters 是按 output_ordinal 的十进制字符串
固定键组成的 object，每章交 decision/reason/constraints。structure payload 只交
reason 和同样固定键的 chapters，每章交 decision/reason。stop payload 只交 reason。
全部章节键及全部字段都必须出现，空约束交 []，无 relation effect 交 null。不要回抄
actor、reviewer_kind、review_run_id、review_token、output_ordinal 或 surface_seal；
Supervisor 只从该 immutable option 注入这些身份，随后仍走现役 typed loader 和审批。
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
    assembled = copy.deepcopy(option['exact_request'])
    action = assembled['action']
    supplied = selected['payload']
    if action['kind'] == 'stop':
        action['reason'] = supplied['reason']
        return assembled
    payload = action['payload']
    chapter_map = supplied.pop('chapters')
    for field, value in supplied.items():
        payload[field] = value
    for row in payload['chapters']:
        row.update(chapter_map[str(row['output_ordinal'])])
    return assembled


__all__ = [
    'WIRE_SCHEMA', 'REFERENCE_CLOSURE', 'Stage4ReviewerWireError',
    'uses_compact_wire', 'output_schema_for', 'response_model', 'prompt_for', 'assemble',
]
