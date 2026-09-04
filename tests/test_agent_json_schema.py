#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent_json_schema.py —— 交给 agent CLI 的响应 schema 必须是它收得下的形状。

【它守的两次事故】2026-08-28 第一次用 main 的 agent 路真跑整书，第一个付费节点
`stage2.global` 直接 exit=2、**一次模型都没调**：

    claude-code CLI exit=1: Error: --json-schema is not a valid JSON Schema:
    strict mode: unknown keyword: "discriminator"

pydantic 给 discriminated union 发 `discriminator`，而 Claude CLI 的 `--json-schema`
走 AJV **strict 模式**——词表外的关键字不是被忽略，是整份 schema 被拒收。

2026-09-04 同一节点改用 Codex executor 时，OpenAI 在模型调用前返回
`invalid_json_schema`：动态键 ``characters: dict[...]`` 被展开成开放 object，既没有
关闭 ``additionalProperties``，又不可能把未知 char_id 全列入 ``required``。同一批扫描
还发现 Stage7 surname_map 的动态键，以及 Stage4/6 默认值留下的非必填嵌套字段。

【为什么钉公共严格子集而不是只钉某个错误】只断言那一个词，下一个 pydantic 版本
（或下一个用了新特性的响应模型）多发一格，就会在**真跑的第一个付费节点上**再炸一次，
而不是在这里。⇒ 扫描面是「全部现役 agent 响应模型 × Claude/Codex 公共关键字 ×
每层 object 关闭/全必填」。动态映射只允许显式键值行数组；正式 artifact 仍可保留 dict。
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from novel.agents.session_client import agent_json_schema

#: 已接入 Claude AJV strict 与 OpenAI Structured Outputs 的公共关键字子集。
#: ⚠ 测试独立维护白名单，不导入生产常量，否则两边一起误放宽会继续假绿。
_VOCABULARY = frozenset({
    '$defs', '$ref',
    'additionalProperties', 'anyOf', 'description', 'enum',
    'exclusiveMaximum', 'exclusiveMinimum', 'format', 'items', 'maxItems',
    'maxLength', 'maximum', 'minItems', 'minLength', 'minimum', 'multipleOf',
    'pattern', 'properties', 'required', 'title', 'type',
})

#: 现役会被送进 agent CLI 的响应模型。**逐个点名**，不从字符串反射——
#: 反射出来的空表会让这条守卫静默失去判别力（判据 3）。
def _models() -> dict[str, type]:
    from contracts.schema.stage2 import (
        ChapterBlueprintsResponseV2Refs, ChapterBlueprintsResponseV31,
        ChapterBoundariesResponse, GlobalCandidateAgentTurnResponse,
        GlobalGroundingCandidateResponse, HingePhraseResponse,
        HingeSelectResponse, RoleSplitResponse,
    )
    from contracts.schema.stage4 import (
        BlindBookStrategyResponse, BlindChapterDesignResponse,
        BlindPlanResponse, ChapterBindingResponse, EvidenceBindingResponse,
    )
    from contracts.schema.stage5 import ChapterResponse
    from contracts.schema.stage6 import (
        HookCandidateRetryResponse, HookResponse, HookSemanticResponse,
    )
    from contracts.schema.stage7 import NameMapResponse
    return {
        'stage2.global': GlobalCandidateAgentTurnResponse,
        'stage2.global/legacy-direct': GlobalGroundingCandidateResponse,
        'stage2.global/role_split': RoleSplitResponse,
        'stage2.blueprint/boundaries': ChapterBoundariesResponse,
        'stage2.blueprint/v2': ChapterBlueprintsResponseV2Refs,
        'stage2.blueprint/v3.1': ChapterBlueprintsResponseV31,
        'stage2.blueprint/hinge_select': HingeSelectResponse,
        'stage2.blueprint/hinge_phrase': HingePhraseResponse,
        'stage4.design/strategy': BlindBookStrategyResponse,
        'stage4.design/chapter': BlindChapterDesignResponse,
        'stage4.design/legacy-rejected-replay': BlindPlanResponse,
        'stage4.plan': EvidenceBindingResponse,
        'stage4.plan/chapter': ChapterBindingResponse,
        'stage5.write': ChapterResponse,
        'stage6.hook': HookResponse,
        'stage6.hook/retry': HookCandidateRetryResponse,
        'stage6.semantic': HookSemanticResponse,
        'stage7.rename': NameMapResponse,
    }


def _keywords(node: object, *, in_names: bool = False) -> set[str]:
    """schema 里出现在**关键字位置**的名字（不含 properties/$defs 的键名）。"""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if not in_names:
                found.add(key)
            found |= _keywords(
                value,
                in_names=key in ('properties', '$defs', 'patternProperties',
                                 'dependentSchemas'),
            )
    elif isinstance(node, list):
        for item in node:
            found |= _keywords(item)
    return found


def _object_contract_errors(node: object, path: str = '$') -> list[str]:
    """独立扫描每个 object；不能只断言生产 validator 没抛异常。"""
    errors: list[str] = []
    if isinstance(node, dict):
        if node.get('type') == 'object':
            properties = node.get('properties')
            required = node.get('required')
            if not isinstance(properties, dict):
                errors.append(f'{path}: dynamic/missing properties')
            if node.get('additionalProperties') is not False:
                errors.append(f'{path}: additionalProperties is not false')
            if (not isinstance(required, list)
                    or not isinstance(properties, dict)
                    or len(required) != len(set(required))
                    or set(required) != set(properties)):
                errors.append(f'{path}: required != properties')
        for key, value in node.items():
            errors.extend(_object_contract_errors(value, f'{path}.{key}'))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            errors.extend(_object_contract_errors(item, f'{path}[{index}]'))
    return errors


def test_every_agent_response_schema_only_uses_json_schema_keywords():
    """任何词表外的关键字都会让 CLI 整份拒收 ⇒ 真跑时一次模型都调不到。"""
    models = _models()
    assert len(models) >= 18, '扫描面缩小了；新增/退役响应模型要同批改这里'
    for label, model_cls in models.items():
        outside = sorted(_keywords(agent_json_schema(model_cls)) - _VOCABULARY)
        assert not outside, f'{label} 的 schema 含词表外关键字 {outside}'


def test_the_discriminator_is_actually_present_before_stripping():
    """反向自检：靶子必须真的存在，否则上一条测的是一个不会发生的情况。"""
    from contracts.schema.stage2 import GlobalCandidateAgentTurnResponse

    raw = GlobalCandidateAgentTurnResponse.model_json_schema(by_alias=True)
    assert 'discriminator' in json.dumps(raw), (
        'pydantic 不再发 discriminator 了？那就确认 CLI 侧的限制是否还在，'
        '而不是顺手删掉归一——删掉之后这条守卫就再也没有判别力')


def test_normalization_preserves_union_branches_and_equivalent_constraints():
    """联合分支不丢；const 变单值 enum，注解删除，约束语义保持。"""
    from contracts.schema.stage2 import GlobalCandidateAgentTurnResponse as M

    raw = M.model_json_schema(by_alias=True)
    sent = agent_json_schema(M)
    raw_text = json.dumps(raw)
    sent_text = json.dumps(sent)
    assert '"discriminator"' in raw_text and '"discriminator"' not in sent_text
    assert raw_text.count('"oneOf"') > 0 and '"oneOf"' not in sent_text
    assert sent_text.count('"anyOf"') == (
        raw_text.count('"anyOf"') + raw_text.count('"oneOf"'))
    assert raw_text.count('"const"') > 0 and '"const"' not in sent_text
    assert sent_text.count('"enum"') == (
        raw_text.count('"enum"') + raw_text.count('"const"'))
    for keyword in ('properties', '$ref'):
        assert sent_text.count(f'"{keyword}"') == raw_text.count(
            f'"{keyword}"'), keyword


def test_every_agent_object_is_closed_and_requires_every_declared_key():
    """Codex/OpenAI 的严格子集约束必须覆盖根、$defs 与联合分支每一层。"""
    for label, model_cls in _models().items():
        schema = agent_json_schema(model_cls)
        assert schema.get('type') == 'object', label
        assert 'anyOf' not in schema, label
        assert not (errors := _object_contract_errors(schema)), (label, errors)


def test_dynamic_business_maps_use_keyed_rows_on_wire_and_round_trip_to_dict():
    """三个动态映射都在线上变行数组，内部/旧 checkpoint 仍是原有 dict。"""
    from contracts.schema.stage2 import (
        EntitiesCandidateResponse, GlobalGroundingCandidateResponse,
        RoleSplitResponse,
    )
    from contracts.schema.stage7 import NameMapResponse

    global_schema = agent_json_schema(GlobalGroundingCandidateResponse)
    assert global_schema['$defs']['EntitiesCandidateResponse'] \
        ['properties']['characters']['type'] == 'array'
    assert agent_json_schema(RoleSplitResponse)['properties'] \
        ['characters']['type'] == 'array'
    assert agent_json_schema(NameMapResponse)['properties'] \
        ['surname_map']['type'] == 'array'

    character = {
        'char_id': 'char_001',
        'canonical': '账房',
        'address': [{
            'surface': '账房', 'by': 'public', 'context': 'address',
            'from_chapter': 1,
        }],
        'name_distinctiveness': 'medium',
        'role': '负责核账',
        'role_evidence': {'evidence_candidate_id': 'e000001'},
        'fate': None,
        'fate_evidence': None,
    }
    entities = EntitiesCandidateResponse.model_validate({
        'characters': [character], 'relations': [],
    })
    assert entities.model_dump(mode='json')['characters'] == {
        'char_001': {key: value for key, value in character.items()
                     if key != 'char_id'},
    }

    split = RoleSplitResponse.model_validate({
        'characters': [{'char_id': 'char_001', 'role': '账房', 'fate': None}],
    })
    assert split.model_dump(mode='json')['characters'] == {
        'char_001': {'role': '账房', 'fate': None},
    }
    # 内部 model_dump / 当前 checkpoint payload 是 dict；typed 0-wire 重放不改形。
    assert RoleSplitResponse.model_validate(split.model_dump(mode='json')) == split

    renamed = NameMapResponse.model_validate({
        'surname_map': [{'old_surname': '沈', 'new_surname': '林'}],
        'characters': [{
            'char_id': 'char_001', 'new_canonical': '林昭', 'renamed': True,
            'not_renamed_reason': None, 'aliases': [], 'reason': '贴合时代',
        }],
    })
    assert renamed.surname_map == {'沈': '林'}

    with pytest.raises(ValidationError, match='不能重复'):
        RoleSplitResponse.model_validate({
            'characters': [
                {'char_id': 'char_001', 'role': '甲', 'fate': None},
                {'char_id': 'char_001', 'role': '乙', 'fate': None},
            ],
        })


@pytest.mark.parametrize('where', ['request', 'policy'])
def test_both_construction_sites_go_through_the_single_landing_point(where):
    """两个调用点都必须走归一：账上记的 schema 与 wire 上那份不能分家。"""
    import inspect

    from novel.agents import session_client

    source = inspect.getsource(session_client)
    assert source.count('model_json_schema(by_alias=True)') == 1, (
        '除 `agent_json_schema` 自己那一处外，不该再有第二个地方直接取 schema')
    assert source.count('agent_json_schema(') >= 3, where
