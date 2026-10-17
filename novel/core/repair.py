"""结构诊断到替换能力的确定性投影；不猜字段语义、不改候选、不执行修复。

错误位置是证据，替换位置是权限。二者必须先按正式 JSON schema 区分：未知键
只能由完整父对象替换而消失；缺字段需要拥有者对象；联合分支名不是 JSON 键。
本模块只给结构上的最小能力，领域 owner 再决定是否须扩大到完整语义聚合。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence


JsonPath = tuple[str | int, ...]


def _resolve(schema: dict, root: dict) -> dict:
    """展开本地引用；外部引用和损坏引用关闭式失败。"""
    current = schema
    seen: set[str] = set()
    while '$ref' in current:
        ref = current['$ref']
        if not isinstance(ref, str) or not ref.startswith('#/') or ref in seen:
            raise ValueError(f'修复 schema 引用不可解析：{ref!r}')
        seen.add(ref)
        target: Any = root
        for key in ref[2:].split('/'):
            key = key.replace('~1', '/').replace('~0', '~')
            if not isinstance(target, dict) or key not in target:
                raise ValueError(f'修复 schema 引用不存在：{ref!r}')
            target = target[key]
        if not isinstance(target, dict):
            raise ValueError(f'修复 schema 引用不是对象：{ref!r}')
        current = {**target, **{key: value for key, value in current.items() if key != '$ref'}}
    return current


def _combine(branches: Sequence[dict], keyword: str = 'anyOf') -> dict:
    distinct: list[dict] = []
    for branch in branches:
        if branch not in distinct:
            distinct.append(branch)
    if not distinct:
        raise ValueError('修复路径没有对应的 schema')
    return distinct[0] if len(distinct) == 1 else {keyword: distinct}


def _step(schema: dict, part: str | int, root: dict) -> dict:
    node = _resolve(schema, root)
    candidates: list[dict] = []
    if isinstance(part, str):
        properties = node.get('properties', {})
        if part in properties and isinstance(properties[part], dict):
            candidates.append(properties[part])
        elif isinstance(node.get('additionalProperties'), dict):
            candidates.append(node['additionalProperties'])
    elif isinstance(part, int) and not isinstance(part, bool) and part >= 0:
        prefix = node.get('prefixItems', ())
        if part < len(prefix) and isinstance(prefix[part], dict):
            candidates.append(prefix[part])
        elif isinstance(node.get('items'), dict):
            candidates.append(node['items'])
    for keyword in ('anyOf', 'oneOf', 'allOf'):
        selected = []
        for branch in node.get(keyword, ()):
            if not isinstance(branch, dict):
                continue
            try:
                selected.append(_step(branch, part, root))
            except ValueError:
                continue
        if selected:
            candidates.append(_combine(selected, 'allOf' if keyword == 'allOf' else 'anyOf'))
    # 同一对象的直接字段约束与 allOf/union 附加约束必须同时成立；只有联合
    # 分支内部使用 anyOf。把这里也合成 anyOf 会放宽动态闭集等正式限制。
    return _combine(candidates, 'allOf')


def _needed_definitions(schema: dict, root: dict) -> dict:
    """只携带子 schema 实际引用的定义及其递归闭包，避免悬空引用。"""
    result: dict[str, dict] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            ref = value.get('$ref')
            if isinstance(ref, str):
                if not ref.startswith('#/$defs/'):
                    raise ValueError(f'修复子 schema 不支持引用：{ref!r}')
                name = ref[len('#/$defs/'):].replace('~1', '/').replace('~0', '~')
                if name not in result:
                    definition = root.get('$defs', {}).get(name)
                    if not isinstance(definition, dict):
                        raise ValueError(f'修复子 schema 定义缺失：{name!r}')
                    result[name] = deepcopy(definition)
                    visit(definition)
            for key, child in value.items():
                if key != '$defs':
                    visit(child)

    visit(schema)
    return result


def schema_at_path(schema: dict, path: JsonPath) -> dict:
    """正式 wire schema 的子 schema；非法路径抛 ValueError，不参考候选坏值。

    联合类型在真实 JSON 路径上合成可达分支；返回独立副本并保留所需 ``$defs``。
    Pydantic 错误里的分支标签须先由 ``shape_repair_paths`` 归一，不能当字段传入。
    """
    node = schema
    for part in path:
        try:
            node = _step(node, part, schema)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'修复路径不属于正式 schema：{path!r}') from exc
    result = deepcopy(_resolve(node, schema))
    result.pop('$defs', None)
    definitions = _needed_definitions(result, schema)
    if definitions:
        result['$defs'] = definitions
    return result


def _branches(schema: dict, root: dict) -> list[dict]:
    node = _resolve(schema, root)
    branches = [branch for key in ('anyOf', 'oneOf') for branch in node.get(key, ())
                if isinstance(branch, dict)]
    return branches


def _branch_labels(schema: dict, root: dict, discriminator: str | None = None) -> set[str | int]:
    node = _resolve(schema, root)
    labels: set[str | int] = set()
    if isinstance(node.get('title'), str):
        labels.add(node['title'])
    kind = node.get('type')
    if isinstance(kind, str):
        labels.update({'integer': ('int', 'integer'), 'number': ('float', 'number'),
                       'boolean': ('bool', 'boolean'), 'null': ('none', 'null'),
                       'string': ('str', 'string'), 'array': ('list', 'tuple', 'array'),
                       'object': ('dict', 'object')}.get(kind, (kind,)))
    prop = node.get('properties', {}).get(discriminator) if discriminator else None
    if isinstance(prop, dict):
        values = [prop['const']] if 'const' in prop else prop.get('enum', ())
        for value in values:
            if isinstance(value, (str, int)):
                labels.update((value, str(value)))
    return labels


def _branch_label_matches(label: str | int, branch: dict, root: dict,
                          discriminator: str | None) -> bool:
    if label in _branch_labels(branch, root, discriminator):
        return True
    # 未命名的容器 union 分支在 Pydantic loc 中使用 list[str]/tuple[int, ...]
    # 一类类型标签。只按已知容器种类识别，不剔除任意未知位置字符串。
    if isinstance(label, str) and '[' in label and label.endswith(']'):
        prefix = label.split('[', 1)[0]
        kind = _resolve(branch, root).get('type')
        return prefix in {'array': ('list', 'tuple'), 'object': ('dict',)}.get(kind, ())
    return False


def _object_schema(schema: dict, root: dict) -> bool:
    node = _resolve(schema, root)
    return (node.get('type') == 'object' or 'properties' in node
            or any(_object_schema(branch, root) for branch in _branches(node, root))
            or any(_object_schema(branch, root) for branch in node.get('allOf', ())
                   if isinstance(branch, dict)))


def _normalise_location(schema: dict, raw: JsonPath, error_type: str) -> JsonPath | None:
    """只剔除 schema 能证明的联合分支标签，不从错误文字猜路径。"""
    def walk(node: dict, rest: JsonPath, path: JsonPath) -> set[JsonPath]:
        if not rest:
            return {path}
        part, tail = rest[0], rest[1:]
        results: set[JsonPath] = set()
        try:
            child = _step(node, part, schema)
        except ValueError:
            child = None
        if child is not None:
            results.update(walk(child, tail, (*path, part)))
        discriminator = _resolve(node, schema).get('discriminator', {}).get('propertyName')
        for branch in _branches(node, schema):
            if _branch_label_matches(part, branch, schema, discriminator):
                results.update(walk(branch, tail, path))
        if (not results and error_type == 'extra_forbidden' and not tail
                and isinstance(part, str) and _object_schema(node, schema)):
            # 仅最后的未知键可以不在 schema 中；其父必须是已证明的正式对象。
            results.add((*path, part))
        return results

    candidates = walk(schema, raw, ())
    return next(iter(candidates)) if len(candidates) == 1 else None


def _walk(wire: Any, path: JsonPath) -> Any:
    node = wire
    for part in path:
        if isinstance(part, int):
            if not isinstance(node, (list, tuple)) or not 0 <= part < len(node):
                raise KeyError(path)
        elif not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def _json_context(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_context(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_context(item) for item in value]
    return str(value)


def shape_repair_paths(schema: dict, wire: Any,
                       errors: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Pydantic 诊断 → 结构最小替换权限；每项保留类型、原坐标和上下文。

    ``replace_path=[]`` 是根对象，只能整份修订；``None`` 表示路径不能证明，
    应报告规划错误。局部 owner 可以扩大范围，不能缩到此边界内而失去必要能力。
    ``too_short`` 只在同一容器有更深层实际错误时去除，保留无关数组的真实长度错误。
    """
    normalized = [(_normalise_location(schema, tuple(error.get('loc') or ()),
                                      str(error.get('type') or '')), error)
                  for error in errors]
    result: list[dict[str, Any]] = []
    for issue_path, error in normalized:
        error_type = str(error.get('type') or '')
        if (error_type == 'too_short' and issue_path is not None
                and any(other is not None and len(other) > len(issue_path)
                        and other[:len(issue_path)] == issue_path
                        for other, _error in normalized)):
            continue
        replace_path = issue_path
        if replace_path is not None:
            if error_type in ('extra_forbidden', 'missing') and replace_path:
                replace_path = replace_path[:-1]
            while replace_path:
                try:
                    _walk(wire, replace_path)
                    schema_at_path(schema, replace_path)
                    break
                except KeyError:
                    replace_path = replace_path[:-1]
                except ValueError:
                    replace_path = None
                    break
            if replace_path == ():
                # 这是整个 wire 的修订权限，不能伪装成一个局部字段。
                replace_path = ()
        result.append({
            'raw_location': list(error.get('loc') or ()),
            'issue_path': list(issue_path) if issue_path is not None else None,
            'error_type': error_type,
            'replace_path': list(replace_path) if replace_path is not None else None,
            'message': str(error.get('msg') or ''),
            'error_context': _json_context(error.get('ctx') or {}),
        })
    return tuple(result)
