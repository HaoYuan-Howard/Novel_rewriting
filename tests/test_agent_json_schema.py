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
from typing import Literal

import pytest
from pydantic import Field, ValidationError, create_model
from typing_extensions import TypeAliasType

from contracts.schema.base import StrictModel
from contracts.schema.orchestration import AgentProfile
from novel.agents import AgentConfigurationError, AgentRequest
from novel.agents.base import validate_agent_output_schema
from novel.agents.session_client import agent_json_schema
from novel.commanders import (
    ClaudeCodeCommanderDriver, CodexCommanderDriver, CommanderConfigurationError,
)

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
    from contracts.schema.analyze import AnalyzeResponse
    from contracts.schema.plot import PlotBlueprintResponse
    from contracts.schema.plot_edit import PlotRevisionResponse
    from contracts.schema.characters import CharactersResponse
    from contracts.schema.knowledge import KnowledgeAgentTurnResponse, KnowledgeResponse
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
        'stage2.analyze': AnalyzeResponse,
        'stage2.global': KnowledgeAgentTurnResponse,
        'stage2.global/legacy-direct': KnowledgeResponse,
        'stage2.characters': CharactersResponse,
        'stage2.blueprint': PlotBlueprintResponse,
        'stage2.blueprint/repair': PlotRevisionResponse,
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


def _ref_siblings(node: object) -> list[set[str]]:
    if isinstance(node, dict):
        own = [set(node) - {'$ref'}] if '$ref' in node and len(node) > 1 else []
        return own + [extra for value in node.values() for extra in _ref_siblings(value)]
    if isinstance(node, list):
        return [extra for value in node for extra in _ref_siblings(value)]
    return []


def _schema_assertions(node, root, *, names=False, active=()):
    """独立展开引用比较业务约束，允许注解归一、全required和无损enum共享。"""
    if isinstance(node, dict):
        if names:
            return {key: _schema_assertions(value, root, active=active)
                    for key, value in node.items()}
        if '$ref' in node:
            ref = node['$ref']
            if ref in active:
                return {'recursive_ref': ref}
            target = root
            for part in ref[2:].split('/'):
                target = target[part.replace('~1', '/').replace('~0', '~')]
            return _schema_assertions(target, root, active=(*active, ref))
        return {
            ('anyOf' if key == 'oneOf' else 'enum' if key == 'const' else key):
            (_schema_assertions([value], root, active=active) if key == 'const'
             else _schema_assertions(value, root, names=key == 'properties', active=active))
            for key, value in node.items()
            if key not in {'$defs', 'title', 'description', 'default', 'discriminator', 'required'}
        }
    if isinstance(node, list):
        return [_schema_assertions(value, root, active=active) for value in node]
    return node


def _enum_values(node: object) -> int:
    if isinstance(node, dict):
        return len(node.get('enum', [])) + sum(_enum_values(value) for value in node.values())
    if isinstance(node, list):
        return sum(_enum_values(value) for value in node)
    return 0


def test_every_agent_response_schema_only_uses_json_schema_keywords():
    """任何词表外的关键字都会让 CLI 整份拒收 ⇒ 真跑时一次模型都调不到。"""
    models = _models()
    # 边界/逐事实/hinge 五种响应退役，现役抽取与显式修订分别验证。
    assert len(models) == 16, '新增 Analyze 首抽；独立人物和知识仍分别覆盖修订 wire'
    for label, model_cls in models.items():
        outside = sorted(_keywords(agent_json_schema(model_cls)) - _VOCABULARY)
        assert not outside, f'{label} 的 schema 含词表外关键字 {outside}'


def test_the_discriminator_is_actually_present_before_stripping():
    """反向自检：靶子必须真的存在，否则上一条测的是一个不会发生的情况。"""
    from contracts.schema.knowledge import KnowledgeAgentTurnResponse

    raw = KnowledgeAgentTurnResponse.model_json_schema(by_alias=True)
    assert 'discriminator' in json.dumps(raw), (
        'pydantic 不再发 discriminator 了？那就确认 CLI 侧的限制是否还在，'
        '而不是顺手删掉归一——删掉之后这条守卫就再也没有判别力')


def test_normalization_preserves_union_branches_and_equivalent_constraints():
    """联合分支不丢；const 变单值 enum，注解删除，约束语义保持。"""
    from contracts.schema.knowledge import KnowledgeAgentTurnResponse as M

    raw = M.model_json_schema(by_alias=True)
    sent = agent_json_schema(M)
    raw_text = json.dumps(raw)
    sent_text = json.dumps(sent)
    assert '"discriminator"' in raw_text and '"discriminator"' not in sent_text
    assert raw_text.count('"oneOf"') > 0 and '"oneOf"' not in sent_text
    assert sent_text.count('"anyOf"') == (
        raw_text.count('"anyOf"') + raw_text.count('"oneOf"'))
    assert raw_text.count('"const"') > 0 and '"const"' not in sent_text
    assert _schema_assertions(raw, raw) == _schema_assertions(sent, sent)
    assert sent_text.count('"properties"') == raw_text.count('"properties"')
    assert not _ref_siblings(sent)


def test_every_agent_object_is_closed_and_requires_every_declared_key():
    """Codex/OpenAI 的严格子集约束必须覆盖根、$defs 与联合分支每一层。"""
    for label, model_cls in _models().items():
        schema = agent_json_schema(model_cls)
        assert schema.get('type') == 'object', label
        assert 'anyOf' not in schema, label
        assert not (errors := _object_contract_errors(schema)), (label, errors)
        assert not _ref_siblings(schema), label
        raw = model_cls.model_json_schema(by_alias=True)
        assert _schema_assertions(raw, raw) == _schema_assertions(schema, schema), label


def test_named_alias_ref_annotations_are_removed_without_expanding_the_closed_domain():
    """复现 observation_alias 的原生400；不只修某一个字段或某一种模型。"""
    alias = TypeAliasType('NativeClosedAlias', Literal[tuple(f'a_{index:016x}' for index in range(200))])
    model = create_model(
        'NativeAliasReferences', __base__=StrictModel,
        **{f'alias_{index}': (alias, Field(..., title=f'端点 {index}', description='当前章闭集端点'))
           for index in range(20)},
    )
    raw = model.model_json_schema(by_alias=True)
    assert _ref_siblings(raw), '必须真的重现 Pydantic 的 $ref sibling'
    sent = agent_json_schema(model)
    assert not _ref_siblings(sent)
    assert _enum_values(sent) == 200
    assert all(set(prop) == {'$ref'} for prop in sent['properties'].values())
    assert _schema_assertions(raw, raw) == _schema_assertions(sent, sent)
    valid = {f'alias_{index}': 'a_0000000000000001' for index in range(20)}
    model.model_validate(valid)
    with pytest.raises(ValidationError):
        model.model_validate({**valid, 'alias_0': 'foreign-alias'})


def test_identical_enum_definitions_are_shared_but_distinct_constraints_are_not():
    values = Literal[tuple(f'choice_{index}' for index in range(10))]
    model = create_model(
        'RepeatedClosedVocabulary', __base__=StrictModel,
        **{f'field_{index}': (values, Field(..., title='共享选择')) for index in range(120)},
    )
    raw = model.model_json_schema(by_alias=True)
    assert _enum_values(raw) == 1200
    sent = agent_json_schema(model)
    assert _enum_values(sent) == 10
    assert _schema_assertions(raw, raw) == _schema_assertions(sent, sent)
    assert len({prop['$ref'] for prop in sent['properties'].values()}) == 1

    class narrowed:
        @classmethod
        def model_json_schema(cls, **_kwargs):
            return _closed_object({
                key: {'type': 'string', 'enum': ['aa', 'bb'], 'pattern': pattern}
                for key, pattern in (
                    ('a', '^a'), ('another_a', '^a'), ('b', '^b'), ('another_b', '^b'))
            })
    converted = agent_json_schema(narrowed)
    assert converted['properties']['a'] == converted['properties']['another_a']
    assert converted['properties']['b'] == converted['properties']['another_b']
    assert converted['properties']['a'] != converted['properties']['b']
    raw_narrowed = narrowed.model_json_schema(by_alias=True)
    assert _schema_assertions(raw_narrowed, raw_narrowed) == _schema_assertions(converted, converted)


@pytest.mark.parametrize('sibling,value', [('pattern', '^a'), ('minLength', 2), ('enum', ['a'])])
def test_reference_assertion_siblings_are_refused_instead_of_silently_dropped(sibling, value):
    class CustomSchema:
        @classmethod
        def model_json_schema(cls, **_kwargs):
            return {
                'type': 'object', 'additionalProperties': False,
                '$defs': {'Alias': {'type': 'string', 'enum': ['a', 'b']}},
                'properties': {'value': {'$ref': '#/$defs/Alias', sibling: value}},
                'required': ['value'],
            }

    with pytest.raises(AgentConfigurationError, match=r'\$ref 必须独占'):
        agent_json_schema(CustomSchema)


@pytest.mark.parametrize('driver_cls,driver_name,model_name', [
    (ClaudeCodeCommanderDriver, 'claude-code', 'claude-opus-5'),
    (CodexCommanderDriver, 'codex', 'gpt-5.6-sol'),
])
def test_raw_ref_sibling_is_rejected_at_executor_and_commander_before_runner(
        tmp_path, driver_cls, driver_name, model_name):
    schema = {
        'type': 'object', 'additionalProperties': False,
        '$defs': {'Alias': {'type': 'string', 'enum': ['a', 'b']}},
        'properties': {'observation_alias': {'$ref': '#/$defs/Alias', 'title': 'Observation Alias'}},
        'required': ['observation_alias'],
    }
    with pytest.raises(AgentConfigurationError, match=r'\$ref 必须独占'):
        AgentRequest(prompt='只读探针', execution_policy='executor-isolated',
                     output_schema=schema, cwd=tmp_path)
    calls = []
    driver = driver_cls(executable=driver_name, runner=lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(CommanderConfigurationError, match=r'\$ref 必须独占'):
        driver.build_invocation(
            AgentProfile(driver=driver_name, model=model_name, reasoning_effort='high'),
            '只读审阅', repo=tmp_path, base=tmp_path, structured_response=True, output_schema=schema)
    assert calls == []


def _closed_object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


@pytest.mark.parametrize('resource,limit,label', [
    ('properties', 5000, '属性总数'), ('enum', 1000, '枚举值总数'),
    ('characters', 120000, '字符串总长'), ('depth', 10, 'object 嵌套层数'),
])
def test_public_native_schema_resource_boundaries_fail_before_wire(resource, limit, label):
    def schema_for(count):
        if resource == 'properties':
            return _closed_object({f'p{index}': {'type': 'string'} for index in range(count)})
        if resource == 'enum':
            return _closed_object({'x': {'type': 'integer', 'enum': list(range(count))}})
        if resource == 'characters':
            return _closed_object({'x': {'type': 'string', 'enum': ['a' * (count - 1)]}})
        result = {'type': 'string'}
        for _ in range(count):
            result = _closed_object({'x': result})
        return result

    validate_agent_output_schema(schema_for(limit))
    with pytest.raises(AgentConfigurationError, match=label):
        validate_agent_output_schema(schema_for(limit + 1))


def test_object_depth_follows_local_refs_without_rejecting_recursive_or_array_schemas():
    definitions = {'Last': _closed_object({'value': {'type': 'string'}})}
    for index in reversed(range(9)):
        target = 'Last' if index == 8 else f'Level{index + 1}'
        definitions[f'Level{index}'] = _closed_object({'nested': {'$ref': '#/$defs/' + target}})
    too_deep = _closed_object({'start': {'$ref': '#/$defs/Level0'}})
    too_deep['$defs'] = definitions
    with pytest.raises(AgentConfigurationError, match='object 嵌套层数 11'):
        validate_agent_output_schema(too_deep)
    recursive = _closed_object({'next': {'anyOf': [{'$ref': '#'}, {'type': 'null'}]}})
    validate_agent_output_schema(recursive)
    arrays = {'type': 'string'}
    for _ in range(20):
        arrays = {'type': 'array', 'items': arrays}
    validate_agent_output_schema(_closed_object({'arrays': arrays}))


def test_large_single_enum_string_budget_applies_only_above_250_values():
    values = [str(index) for index in range(251)]
    values[0] += 'x' * (15000 - sum(map(len, values)))
    schema = _closed_object({'x': {'type': 'string', 'enum': values}})
    validate_agent_output_schema(schema)
    values[0] += 'x'
    with pytest.raises(AgentConfigurationError, match='单枚举.*15001.*15000'):
        validate_agent_output_schema(schema)
    # 正好250项只受整份schema的总长限制，不套用>250项的专属上限。
    at_threshold = [str(index).ljust(100, 'x') for index in range(250)]
    validate_agent_output_schema(_closed_object({'x': {'type': 'string', 'enum': at_threshold}}))


@pytest.mark.parametrize('reference', ['#/$defs/missing', 'https://example.test/external-schema', 7])
def test_unreferenced_definitions_still_require_valid_local_reference_targets(reference):
    schema = _closed_object({'x': {'type': 'string'}})
    schema['$defs'] = {'unused': {'$ref': reference}}
    with pytest.raises(AgentConfigurationError, match='引用|目标不存在'):
        validate_agent_output_schema(schema)


def test_dynamic_business_maps_use_keyed_rows_on_wire_and_round_trip_to_dict():
    """实体局部句柄由代码编译；全书 wire 不回到动态知识数据库。"""
    from contracts.schema.characters import CharactersResponse
    from contracts.schema.analyze import AnalyzeResponse
    from contracts.schema.knowledge import KnowledgeResponse
    from contracts.schema.stage7 import NameMapResponse

    assert agent_json_schema(CharactersResponse)['properties']['characters']['type'] == 'array'
    knowledge_schema = agent_json_schema(KnowledgeResponse)
    assert set(knowledge_schema['properties']) == {'setting', 'relations', 'world_rules', 'arc_secrets'}
    assert agent_json_schema(NameMapResponse)['properties']['surname_map']['type'] == 'array'
    character = {
        'character_ref': 'c1', 'canonical': '账房',
        'aliases': [{'surface': '账房', 'identity_revealed_from': 1, 'evidence_refs': ['e000001']}],
        'first_chapter': 1, 'first_appearance_evidence_refs': ['e000001'],
        'name_distinctiveness': 'medium', 'role': '负责核账',
        'role_evidence_refs': ['e000001'],
    }
    registry = CharactersResponse.model_validate({'characters': [character]})
    assert registry.model_dump(mode='json')['characters'] == [character]
    joint = AnalyzeResponse.model_validate({'characters': [character], 'global': {
        'setting': {'era': None, 'era_evidence_refs': [], 'social_context': None,
                    'social_context_evidence_refs': []},
        'relations': [], 'world_rules': [], 'arc_secrets': [],
    }})
    assert AnalyzeResponse.model_validate(joint.model_dump(mode='json', by_alias=True)) == joint
    assert CharactersResponse.model_validate(registry.model_dump(mode='json')) == registry
    renamed = NameMapResponse.model_validate({
        'surname_map': [{'old_surname': '沈', 'new_surname': '林'}],
        'characters': [{'char_id': 'char_001', 'new_canonical': '林昭', 'renamed': True,
                        'not_renamed_reason': None, 'aliases': [], 'reason': '贴合时代'}],
    })
    assert renamed.surname_map == {'沈': '林'}
    with pytest.raises(ValidationError, match='重复'):
        CharactersResponse.model_validate({'characters': [character, dict(character)]})
    with pytest.raises(ValidationError, match='重复'):
        AnalyzeResponse.model_validate({**joint.model_dump(mode='json', by_alias=True),
                                       'characters': [character, dict(character)]})


@pytest.mark.parametrize('where', ['request', 'policy'])
def test_both_construction_sites_go_through_the_single_landing_point(where):
    """两个调用点都必须走归一：账上记的 schema 与 wire 上那份不能分家。"""
    import inspect

    from novel.agents import session_client

    source = inspect.getsource(session_client)
    assert source.count('model_json_schema(by_alias=True)') == 1, (
        '除 `agent_json_schema` 自己那一处外，不该再有第二个地方直接取 schema')
    assert source.count('agent_json_schema(') >= 3, where


def _glm_native_argv(schema, *, commander, directory, monkeypatch):
    """只构造真实默认驱动 invocation；runner 是禁止执行的反向哨兵。"""
    from novel.agents.base import CLAUDE_STRUCTURED_DELIVERY_V1, ensure_command_line_fits
    from novel.agents.glm import BIGMODEL_BASE_URL, GlmClaudeCodeDriver
    from novel.commanders.glm import GlmClaudeCodeCommanderDriver

    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(directory / 'no-cli-config'))
    monkeypatch.setenv('ANTHROPIC_BASE_URL', BIGMODEL_BASE_URL)
    profile = AgentProfile(driver='claude-code-glm', model='glm-5.3-flash[1M]',
        reasoning_effort='high', execution_mode='standard')
    def forbidden_runner(*args, **kwargs):
        raise AssertionError('schema 预检不得启动模型或进程')
    if commander:
        driver = GlmClaudeCodeCommanderDriver(executable='claude', runner=forbidden_runner)
        invocation = driver.build_invocation(profile, '完整审阅材料通过 stdin 提供。',
            repo=directory, base=directory, structured_response=True, output_schema=schema)
        argv = invocation.argv
        assert invocation.output_schema == schema
    else:
        driver = GlmClaudeCodeDriver(executable='claude', runner=forbidden_runner)
        driver._check_profile(profile)
        request = AgentRequest(prompt='完整章节材料通过 stdin 提供。', execution_policy='executor-isolated',
            cwd=directory, output_schema=schema, delivery_protocol=CLAUDE_STRUCTURED_DELIVERY_V1)
        argv = driver._argv(profile, request, session_id=None)
    assert argv[argv.index('--model') + 1] == profile.model
    assert argv[argv.index('--effort') + 1] == profile.reasoning_effort
    assert json.loads(argv[argv.index('--json-schema') + 1]) == schema
    ensure_command_line_fits(argv, platform_name='nt')
    return argv


@pytest.mark.parametrize('label', [
    'stage2.blueprint', 'stage2.blueprint/repair',
    'stage4.design/strategy', 'stage4.design/chapter', 'stage4.plan', 'stage4.plan/chapter',
    'stage4.design/commander', 'stage4.plan/commander',
])
def test_gate_b_real_response_schemas_reach_default_glm_without_model_request(label, tmp_path, monkeypatch):
    from novel.orchestrator import queue, stage4_reviewer_wire
    from novel.stages.stage2.contract import contract_for
    from tests.test_stage4_reviewer_wire import _task

    commander = label.endswith('/commander')
    if label == 'stage2.blueprint':
        model = contract_for('plot-blueprint-v1').response_model
        assert model is _models()[label], '验证真实 wrapper，不能只扫描其中一个子模型'
        schema = agent_json_schema(model)
    elif label in {'stage4.design/commander', 'stage4.plan/commander'}:
        task = _task(queue.WAITING_ON_STAGE4_DESIGN_REVIEW if label.startswith('stage4.design')
                     else queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW)
        schema = stage4_reviewer_wire.output_schema_for(task)
    else:
        schema = agent_json_schema(_models()[label])
    assert schema is not None
    assert not _object_contract_errors(schema)
    assert not _keywords(schema) - _VOCABULARY
    _glm_native_argv(schema, commander=commander, directory=tmp_path, monkeypatch=monkeypatch)
