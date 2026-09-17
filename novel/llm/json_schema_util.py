#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent / executor 响应 JSON Schema 的唯一构造与公共严格子集预检。

identity、``schema_sha256`` 与真正发出去的字节必须是同一份；生产 journal
与 stage producer 只经本模块，不经 ``novel.agents``。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from novel.errors import AgentConfigurationError

#: Claude 的 AJV strict 与 OpenAI Structured Outputs 都能接收的公共子集。
#: ``agent_json_schema`` 会把 Pydantic 的等价注解先归一到这个集合；任何仍在
#: 集合外的关键字都必须在发起 CLI 前关闭式拒绝，不能让某个 driver 私下删改。
_PORTABLE_AGENT_SCHEMA_KEYWORDS = frozenset({
    '$defs', '$ref',
    'additionalProperties', 'anyOf', 'description', 'enum',
    'exclusiveMaximum', 'exclusiveMinimum', 'format', 'items', 'maxItems',
    'maxLength', 'maximum', 'minItems', 'minLength', 'minimum', 'multipleOf',
    'pattern', 'properties', 'required', 'title', 'type',
})
_SCHEMA_NAME_CONTAINERS = frozenset({'$defs', 'properties'})
# OpenAI Structured Outputs 当前公开的整份 schema 上限；Claude/Codex 共用
# 同一关闭式入口。数组本身不计 object nesting，避免发明未经标定的新限制。
_AGENT_SCHEMA_MAX_PROPERTIES = 5000
_AGENT_SCHEMA_MAX_OBJECT_DEPTH = 10
_AGENT_SCHEMA_MAX_STRING_CHARACTERS = 120000
_AGENT_SCHEMA_MAX_ENUM_VALUES = 1000
_AGENT_SCHEMA_LARGE_ENUM_VALUES = 250
_AGENT_SCHEMA_MAX_LARGE_ENUM_STRING_CHARACTERS = 15000


def validate_agent_output_schema(schema: Mapping[str, Any]) -> None:
    """验证所有 driver 共用的严格结构化输出 schema；失败发生在 CLI 之前。

    公共边界选择已接入 provider 的交集：根必须是 object；每一层 object 都要
    ``additionalProperties=false``，并显式要求全部 properties。动态键映射必须由
    业务模型先改成键值行数组，不能在这里猜键字段后静默改变响应语义。
    ``$ref`` 节点必须是纯引用；注解归一只属于 ``agent_json_schema``，driver
    收到未经归一的引用或不可保留的约束 sibling 时必须在 runner 前拒绝。
    """
    if schema.get('type') != 'object' or 'anyOf' in schema:
        raise AgentConfigurationError(
            'agent output_schema 根必须是 object，且根层不得使用 anyOf')
    property_count = 0
    enum_count = 0
    string_characters = 0
    references: list[object] = []

    def visit(node: object, path: str, *, names: bool = False) -> None:
        nonlocal property_count, enum_count, string_characters
        if isinstance(node, Mapping):
            if names:
                if any(not isinstance(key, str) for key in node):
                    raise AgentConfigurationError(
                        f'agent output_schema {path} 含非字符串定义/属性名')
                string_characters += sum(len(key) for key in node)
            if not names:
                for key in node:
                    if not isinstance(key, str):
                        raise AgentConfigurationError(
                            f'agent output_schema {path} 含非字符串关键字')
                    if key not in _PORTABLE_AGENT_SCHEMA_KEYWORDS:
                        raise AgentConfigurationError(
                            f'agent output_schema {path}.{key} 不属于 '
                            'Claude/Codex 公共严格 JSON Schema 子集')
                if '$ref' in node and set(node) != {'$ref'}:
                    raise AgentConfigurationError(
                        f'agent output_schema {path} 的 $ref 必须独占 schema object；'
                        '引用旁注解须在公共构造口归一，业务约束不得静默删除')
                if '$ref' in node:
                    references.append(node['$ref'])
                properties = node.get('properties')
                if isinstance(properties, Mapping):
                    property_count += len(properties)
                enum = node.get('enum')
                if isinstance(enum, list):
                    enum_count += len(enum)
                    enum_string_characters = sum(
                        len(value) for value in enum if isinstance(value, str))
                    string_characters += enum_string_characters
                    if (len(enum) > _AGENT_SCHEMA_LARGE_ENUM_VALUES
                            and enum_string_characters > _AGENT_SCHEMA_MAX_LARGE_ENUM_STRING_CHARACTERS):
                        raise AgentConfigurationError(
                            f'agent output_schema {path} 单枚举有 {len(enum)} 个值，'
                            f'字符串总长 {enum_string_characters} 超过公共上限 '
                            f'{_AGENT_SCHEMA_MAX_LARGE_ENUM_STRING_CHARACTERS}；不能发起 CLI')

                type_value = node.get('type')
                is_object = (
                    type_value == 'object'
                    or (isinstance(type_value, list)
                        and 'object' in type_value)
                )
                if is_object:
                    properties = node.get('properties')
                    if not isinstance(properties, Mapping):
                        raise AgentConfigurationError(
                            f'agent output_schema {path} 是动态或未声明键的 object；'
                            '请改用带显式键字段的行数组')
                    if node.get('additionalProperties') is not False:
                        raise AgentConfigurationError(
                            f'agent output_schema {path}.additionalProperties '
                            '必须显式为 false')
                    required = node.get('required')
                    if (not isinstance(required, list)
                            or any(not isinstance(item, str)
                                   for item in required)
                            or len(required) != len(set(required))
                            or set(required) != set(properties)):
                        raise AgentConfigurationError(
                            f'agent output_schema {path}.required 必须无重复且逐项覆盖 '
                            'properties 的全部键')

            for key, value in node.items():
                visit(
                    value,
                    f'{path}.{key}',
                    names=(not names and key in _SCHEMA_NAME_CONTAINERS),
                )
        elif isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f'{path}[{index}]')

    visit(schema, '$')

    def reference_target(value: object) -> Mapping[str, Any]:
        if not isinstance(value, str) or not (value == '#' or value.startswith('#/')):
            raise AgentConfigurationError('agent output_schema 只接受本地 JSON Pointer 引用')
        target: object = schema
        for part in value[2:].split('/') if value != '#' else ():
            key = part.replace('~1', '/').replace('~0', '~')
            if not isinstance(target, Mapping) or key not in target:
                raise AgentConfigurationError('agent output_schema 的 $ref 目标不存在')
            target = target[key]
        if not isinstance(target, Mapping):
            raise AgentConfigurationError('agent output_schema 的 $ref 目标不是 schema object')
        return target

    def object_depth(node: object, active: frozenset[str] = frozenset()) -> int:
        if not isinstance(node, Mapping):
            return 0
        if '$ref' in node:
            ref = node['$ref']
            target = reference_target(ref)
            if ref in active:
                # provider 明示支持递归 schema；只截止同一展开路径上的回边，
                # 不把合法自引用当无限嵌套，也不跳过其他不同引用分支。
                return 0
            return object_depth(target, active | {ref})
        type_value = node.get('type')
        own = int(type_value == 'object' or (
            isinstance(type_value, list) and 'object' in type_value))
        children: list[object] = []
        properties = node.get('properties')
        if isinstance(properties, Mapping):
            children.extend(properties.values())
        if 'items' in node:
            children.append(node['items'])
        branches = node.get('anyOf')
        if isinstance(branches, list):
            children.extend(branches)
        return own + max((object_depth(child, active) for child in children), default=0)

    # provider会校验整份$defs；未被root引用的定义也不能携悬空/外部引用。
    for reference in references:
        reference_target(reference)
    depth = object_depth(schema, frozenset({'#'}))
    for label, actual, limit in (
        ('属性总数', property_count, _AGENT_SCHEMA_MAX_PROPERTIES),
        ('枚举值总数', enum_count, _AGENT_SCHEMA_MAX_ENUM_VALUES),
        ('属性/定义/枚举字符串总长', string_characters, _AGENT_SCHEMA_MAX_STRING_CHARACTERS),
        ('object 嵌套层数', depth, _AGENT_SCHEMA_MAX_OBJECT_DEPTH),
    ):
        if actual > limit:
            raise AgentConfigurationError(
                f'agent output_schema {label} {actual} 超过公共上限 {limit}；'
                '必须保留业务约束并调整 wire，不能发起 CLI')


def _share_agent_enum_definitions(schema: dict[str, Any]) -> dict[str, Any]:
    """完全相同的枚举载体只声明一次，保留全部约束与注解。

    provider 的枚举数上限按整份 schema 累计。同一个 code/decision/kind 在每章
    复制一次也会耗尽上限；这里按完整载体内容寻址复用，只替换叶子枚举节点，
    不合并不同 pattern/范围/描述，也不把 enum 扩成自由字符串或内联引用目标。
    """
    counts: dict[str, int] = {}
    bodies: dict[str, dict[str, Any]] = {}

    def enum_key(node: object, *, names: bool) -> str | None:
        if (names or not isinstance(node, dict) or 'enum' not in node
                or any(key in node for key in (
                    '$ref', '$defs', 'properties', 'items', 'anyOf'))):
            return None
        return json.dumps(node, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)

    def collect(node: object, *, names: bool = False) -> None:
        key = enum_key(node, names=names)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
            assert isinstance(node, dict)
            bodies[key] = node
        if isinstance(node, dict):
            for name, value in node.items():
                collect(value, names=not names and name in ('$defs', 'properties'))
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema)
    shared = {
        key: 'AgentSharedEnum_' + hashlib.sha256(key.encode('utf-8')).hexdigest()
        for key, count in counts.items() if count > 1
    }
    if not shared:
        return schema
    existing = schema.get('$defs', {})
    if not isinstance(existing, dict) or any(name in existing for name in shared.values()):
        raise AgentConfigurationError('agent 枚举共享定义与原 $defs 身份冲突')

    def replace(node: object, *, names: bool = False) -> Any:
        key = enum_key(node, names=names)
        if key in shared:
            return {'$ref': '#/$defs/' + shared[key]}
        if isinstance(node, dict):
            return {
                name: replace(value, names=not names and name in ('$defs', 'properties'))
                for name, value in node.items()
            }
        if isinstance(node, list):
            return [replace(value) for value in node]
        return node

    result = replace(schema)
    result.setdefault('$defs', {}).update({name: bodies[key] for key, name in shared.items()})
    return result


def agent_json_schema(model_cls: type) -> dict:
    """交给 agent CLI 的响应 JSON Schema。**全仓唯一构造口。**

    ⚠ **必须剥掉 `discriminator`。** pydantic 为 discriminated union 发这一格
    （Stage2 global 那份响应里就有 3 处：`KnowledgeEventResponse.subject`、
    `RelationResponse.visibility`、`SecretResponse.setup`），而 Claude CLI 的
    `--json-schema` 走 AJV **strict 模式**，遇到词表外的关键字直接拒收整份 schema：
    `unknown keyword: "discriminator"` ⇒ CLI exit=1，**一次模型都没调就整节点终态**。
    这是 2026-08-28 第一次用 main 的 agent 路真跑整书时当场撞到的第一堵墙。

    ⚠ **剥掉它不放宽任何约束**：`discriminator` 只是给校验器的分支提示；
    Pydantic 同时发出的 `oneOf` 归一成公共子集支持的 `anyOf`，联合分支本身一格没动；
    单值 `const` 等价改写为单元素 `enum`。`default` 只是注解，wire 必须把对应
    nullable/空集合键显式交回，所以删除注解并把每层 `properties` 全量写进
    `required`。真正的判定权从来不在
    provider 侧——响应回来仍要过 `novel/llm/client.py::judge_payload` 的四道闸，
    其中 schema 那一道用的是 pydantic 模型本身，不是这份下发的 schema。
    ⇒ 这里改的是「怎么把契约讲给 CLI 听」，不是「契约是什么」。

    ``$ref`` 必须独占它所在的 schema object。Pydantic 会为命名类型引用补
    ``title`` / ``description``，这在 JSON Schema 中合法，却会被 Codex 原生
    strict schema 拒绝。只删除引用旁的这两种注解，保留命名 ``$defs`` 和引用，
    不展开大闭集；其余 sibling 可能是业务约束，必须由公共预检关闭式拒绝，
    不能跟着注解一起丢掉。完全相同的叶子枚举经内容寻址共用一份定义，避免
    逐章重复的 code/decision/kind 膨胀成超过 provider 上限的枚举总数。

    动态键 object 不能靠删关键字修成合法 schema：调用点必须先把 provider wire 设计成
    显式键值行数组，再由 Pydantic 确定性还原为内部映射。归一完成后还要经过公共严格
    子集预检；因此未来新增不兼容响应会在任何 Claude/Codex 子进程前显式失败。

    ⚠ 归一在**构造口**而不是某个 driver 里：identity、`schema_sha256` 与真正发出去的
    字节必须是同一份。放进 driver 会让账上记的 schema 与 wire 上的那份分家。
    """
    def normalize(node: object, *, names: bool = False) -> object:
        if isinstance(node, dict):
            if names:
                return {
                    key: normalize(value)
                    for key, value in node.items()
                }
            if 'oneOf' in node and 'anyOf' in node:
                raise AgentConfigurationError(
                    'agent response schema 同层同时出现 oneOf/anyOf，无法等价归一')
            if 'const' in node and 'enum' in node:
                raise AgentConfigurationError(
                    'agent response schema 同层同时出现 const/enum，无法等价归一')
            out: dict[str, object] = {}
            for key, value in node.items():
                if key in ('default', 'discriminator'):
                    continue
                if '$ref' in node and key in ('title', 'description'):
                    continue
                if key == 'const':
                    # OpenAI 的公开严格子集登记 enum，不登记 const；单值枚举与
                    # Pydantic Literal 发出的 const 约束等价，Claude AJV 也接受。
                    out['enum'] = [normalize(value)]
                else:
                    normalized_key = 'anyOf' if key == 'oneOf' else key
                    out[normalized_key] = normalize(
                        value,
                        names=key in ('$defs', 'properties'),
                    )
            properties = out.get('properties')
            type_value = out.get('type')
            if (isinstance(properties, dict)
                    and (type_value == 'object'
                         or (isinstance(type_value, list)
                             and 'object' in type_value))):
                # OpenAI strict 要求所有键显式返回；nullable 用 anyOf/null 表达，
                # 空列表也必须返回 []，不能靠省键触发 Pydantic default。
                out['required'] = list(properties)
            return out
        if isinstance(node, list):
            return [normalize(item) for item in node]
        return node

    schema = normalize(model_cls.model_json_schema(by_alias=True))
    assert isinstance(schema, dict)
    schema = _share_agent_enum_definitions(schema)
    validate_agent_output_schema(schema)
    return schema


__all__ = ['agent_json_schema', 'validate_agent_output_schema']
