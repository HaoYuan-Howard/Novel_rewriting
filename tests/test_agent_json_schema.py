#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent_json_schema.py —— 交给 agent CLI 的响应 schema 必须是它收得下的形状。

【它守的那次事故】2026-08-28 第一次用 main 的 agent 路真跑整书，第一个付费节点
`stage2.global` 直接 exit=2、**一次模型都没调**：

    claude-code CLI exit=1: Error: --json-schema is not a valid JSON Schema:
    strict mode: unknown keyword: "discriminator"

pydantic 给 discriminated union 发 `discriminator`，而 Claude CLI 的 `--json-schema`
走 AJV **strict 模式**——词表外的关键字不是被忽略，是整份 schema 被拒收。

【为什么钉「词表」而不是只钉 `discriminator`】只断言那一个词，下一个 pydantic 版本
（或下一个用了新特性的响应模型）多发一格，就会在**真跑的第一个付费节点上**再炸一次，
而不是在这里。⇒ 扫描面是「全部现役 agent 响应模型 × 全部 JSON Schema 关键字」。
"""
from __future__ import annotations

import json

import pytest

from novel.agents.session_client import agent_json_schema

#: JSON Schema draft 2020-12 的关键字表（AJV strict 认的那一套）。
#: ⚠ 这是**白名单**：新关键字必须显式加进来并说明 CLI 收不收，不能靠沉默通过。
_VOCABULARY = frozenset({
    '$anchor', '$comment', '$defs', '$dynamicAnchor', '$dynamicRef', '$id',
    '$ref', '$schema', '$vocabulary',
    'additionalProperties', 'allOf', 'anyOf', 'const', 'contains',
    'contentEncoding', 'contentMediaType', 'contentSchema', 'default',
    'dependentRequired', 'dependentSchemas', 'deprecated', 'description',
    'else', 'enum', 'examples', 'exclusiveMaximum', 'exclusiveMinimum',
    'format', 'if', 'items', 'maxContains', 'maxItems', 'maxLength',
    'maxProperties', 'maximum', 'minContains', 'minItems', 'minLength',
    'minProperties', 'minimum', 'multipleOf', 'not', 'oneOf', 'pattern',
    'patternProperties', 'prefixItems', 'properties', 'propertyNames',
    'readOnly', 'required', 'then', 'title', 'type', 'unevaluatedItems',
    'unevaluatedProperties', 'uniqueItems', 'writeOnly',
})

#: 现役会被送进 agent CLI 的响应模型。**逐个点名**，不从字符串反射——
#: 反射出来的空表会让这条守卫静默失去判别力（判据 3）。
def _models() -> dict[str, type]:
    from contracts.schema.stage2 import (
        GlobalAgentTurnResponse, HingePhraseResponse, HingeSelectResponse,
    )
    from contracts.schema.stage4 import BlindPlanResponse, EvidenceBindingResponse
    from contracts.schema.stage5 import ChapterResponse
    from contracts.schema.stage6 import (
        HookCandidateRetryResponse, HookResponse, HookSemanticResponse,
    )
    from contracts.schema.stage7 import NameMapResponse
    from novel.stages.stage2.extract import (
        RoleSplitResponse,
    )

    return {
        'stage2.global': GlobalAgentTurnResponse,
        'stage2.global/role_split': RoleSplitResponse,
        'stage2.blueprint/hinge_select': HingeSelectResponse,
        'stage2.blueprint/hinge_phrase': HingePhraseResponse,
        'stage4.design': BlindPlanResponse,
        'stage4.plan': EvidenceBindingResponse,
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


def test_every_agent_response_schema_only_uses_json_schema_keywords():
    """任何词表外的关键字都会让 CLI 整份拒收 ⇒ 真跑时一次模型都调不到。"""
    models = _models()
    assert len(models) >= 11, '扫描面缩小了；新增/退役响应模型要同批改这里'
    for label, model_cls in models.items():
        outside = sorted(_keywords(agent_json_schema(model_cls)) - _VOCABULARY)
        assert not outside, f'{label} 的 schema 含词表外关键字 {outside}'


def test_the_discriminator_is_actually_present_before_stripping():
    """反向自检：靶子必须真的存在，否则上一条测的是一个不会发生的情况。"""
    from novel.stages.stage2.extract import GlobalGroundingResponse

    raw = GlobalGroundingResponse.model_json_schema(by_alias=True)
    assert 'discriminator' in json.dumps(raw), (
        'pydantic 不再发 discriminator 了？那就确认 CLI 侧的限制是否还在，'
        '而不是顺手删掉归一——删掉之后这条守卫就再也没有判别力')


def test_stripping_removes_no_structural_constraint():
    """剥掉的只是分支提示：联合分支、必填与类型一格不少。"""
    from novel.stages.stage2.extract import GlobalGroundingResponse as M

    raw = M.model_json_schema(by_alias=True)
    sent = agent_json_schema(M)
    for keyword in ('anyOf', 'oneOf', 'required', 'properties', 'enum', '$ref'):
        assert json.dumps(sent).count(f'"{keyword}"') == \
            json.dumps(raw).count(f'"{keyword}"'), keyword


@pytest.mark.parametrize('where', ['request', 'policy'])
def test_both_construction_sites_go_through_the_single_landing_point(where):
    """两个调用点都必须走归一：账上记的 schema 与 wire 上那份不能分家。"""
    import inspect

    from novel.agents import session_client

    source = inspect.getsource(session_client)
    assert source.count('model_json_schema(by_alias=True)') == 1, (
        '除 `agent_json_schema` 自己那一处外，不该再有第二个地方直接取 schema')
    assert source.count('agent_json_schema(') >= 3, where
