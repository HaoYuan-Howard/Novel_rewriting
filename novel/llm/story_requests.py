"""当前故事契约的耐久语义请求、材料补全与受限 JSON 补丁。

实际外发、授权、额度及账仍只有 PaidRequestSession 一个边界。本组合层不解读
小说；只执行显式材料查询和已定位字段替换，任何候选均重过同一 compiler。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

from pydantic import ValidationError

from contracts.schema.source_story import STORY_VERSION
from novel.core.source_index import SourceIndex, read_segments, search_literal
from novel.llm.paid_requests import PaidRequestSession, ValidationStop, digest, semantic_owner_unit
from novel.llm.transport import ChatRequest
from novel.llm.budget import enforce_request_budget


MATERIAL_RULES = '''
若当前材料不足，可返回 {"kind":"need_materials","requests":[...]}。
查询只可为 {"operation":"read_segments","segment_ids":[已有段号],"adjacent_paragraphs":0}
或 {"operation":"search_literal","terms":[逐字词],"mode":"all|any",
"chapter_range":[起章,止章],"cursor":0,"limit":20}。
查询不授予事实、身份或知情；empty 不证明不存在，truncated 须继续翻页或保持未决。
材料足够时直接返回本任务协议的完整 JSON。禁止读取文件、运行命令或请求其它书。
'''


def material_reference_mode(owner: str) -> Literal['chapter_numbers', 'canonical']:
    """引用显示协议只取真实任务owner，不从查询上界或模型自报字段猜测。"""
    return ('chapter_numbers' if semantic_owner_unit(owner)[0] == 'str06.stage2.chapters'
            else 'canonical')


def material_rules(owner: str) -> str:
    if material_reference_mode(owner) == 'chapter_numbers':
        return MATERIAL_RULES.replace('[已有段号]', '[已给严格正整数引用]')
    return MATERIAL_RULES


@dataclass(frozen=True)
class SemanticResult:
    value: dict
    refs: tuple
    answer: Any
    trace: tuple[dict, ...] = ()
    materials: tuple[dict, ...] = ()


@dataclass(frozen=True)
class RevisionSeed:
    """已验真实回答的修订基线；显式 repair 模式不接受完整替代候选。"""
    kind: Literal['repair']
    baseline: dict
    paths: tuple[tuple[str | int, ...], ...]
    refs: tuple
    trace: tuple[dict, ...]
    append_paths: tuple[tuple[str | int, ...], ...]
    remove_paths: tuple[tuple[str | int, ...], ...]
    atomic_paths: tuple[tuple[str | int, ...], ...] = ()

    def __post_init__(self):
        if self.kind != 'repair' or not isinstance(self.baseline, dict):
            raise ValueError('修订必须有明确 repair 种类及对象基线')
        if not self.paths and not self.append_paths and not self.remove_paths:
            raise ValueError('修订没有明确授权位置，范围不足须停止')
        if not self.refs or replay_semantic_trace(self.trace) != self.baseline:
            raise ValueError('修订基线不能从真实回答trace完整重建')
        all_paths = (*self.paths, *self.append_paths, *self.remove_paths)
        normalized = tuple(_json_path(path) for path in all_paths)
        if len(set(normalized)) != len(normalized):
            raise ValueError('修订路径不得为空或重复')
        atomic = _validated_atomic_paths(self.baseline, self.paths, self.atomic_paths)
        for path in self.paths:
            value = _at(self.baseline, path)
            if isinstance(value, (list, tuple)) and path not in atomic:
                raise ValueError('修订不得整体替换列表；须定位既有条目或显式append')
        for path in self.append_paths:
            if not isinstance(_at(self.baseline, path), (list, dict)):
                raise ValueError('append授权必须指向既有数组或语义键表')
        for path in self.remove_paths:
            parent = _at(self.baseline, path[:-1])
            if len(path) < 2 or not isinstance(parent, dict) or not isinstance(path[-1], str) or path[-1] not in parent:
                raise ValueError('remove授权只能删除既有字典语义键条目，不得压缩列表位置')
            if any(other[:len(path)] == path or path[:len(other)] == other
                   for other in (*self.paths, *self.append_paths)):
                raise ValueError('remove不得与替换或追加范围重叠')


def _json_path(value: Any, *, allow_empty: bool = False) -> tuple:
    """JSON 路径不接受 Python 将 bool/float 与整数索引视为相等的别名。"""
    if not isinstance(value, (list, tuple)) or not value and not allow_empty:
        raise ValueError('路径须为非空数组')
    if any(type(part) not in (str, int) or type(part) is int and part < 0 for part in value):
        raise ValueError('路径只接受字符串键与非负整数索引，不接受布尔或浮点数')
    return tuple(value)


def _at(value: Any, path: tuple):
    path = _json_path(path, allow_empty=True)
    for component in path:
        if isinstance(value, dict) and type(component) is not str:
            raise ValueError('对象路径须使用字符串键')
        if isinstance(value, (list, tuple)) and type(component) is not int:
            raise ValueError('数组路径须使用整数索引')
        value = value[component]
    return value


def _validated_atomic_paths(candidate: dict, paths: tuple[tuple, ...],
                             atomic_paths: tuple[tuple, ...]) -> tuple[tuple, ...]:
    """原子字段是显式replace权限的子集；共享层不决定领域字段能否获得该权限。"""
    atomic = tuple(_json_path(path) for path in atomic_paths)
    if len(set(atomic)) != len(atomic) or not set(atomic) <= set(paths):
        raise ValueError('原子字段必须是当前replace授权的无重复子集')
    if any(not isinstance(_at(candidate, path), (list, tuple)) for path in atomic):
        raise ValueError('原子字段必须精确定位既有语义数组字段')
    return atomic


def _frozen_list_layout(before: Any, after: Any, *, atomic_paths: tuple[tuple, ...] = (),
                         path: tuple = ()) -> None:
    """普通数组位置冻结；只有显式原子字段可以整体重分段。"""
    if path in atomic_paths:
        if not isinstance(before, (list, tuple)) or not isinstance(after, (list, tuple)):
            raise ValueError('原子字段替换后仍须为语义数组')
        return
    if isinstance(before, (list, tuple)):
        if not isinstance(after, (list, tuple)) or len(before) != len(after):
            raise ValueError('替换不得改变既有数组长度；结构新增须显式append')
        if list(before) != list(after) and sorted(map(digest, before)) == sorted(map(digest, after)):
            raise ValueError('不得重排既有列表位置')
        for number, (old, new) in enumerate(zip(before, after)):
            _frozen_list_layout(old, new, atomic_paths=atomic_paths, path=(*path, number))
    elif isinstance(before, dict) and isinstance(after, dict):
        for key in before.keys() & after.keys():
            _frozen_list_layout(before[key], after[key], atomic_paths=atomic_paths, path=(*path, key))


def _revision_patch(candidate: dict, value: dict, paths: tuple[tuple, ...],
                    append_paths: tuple[tuple, ...], remove_paths: tuple[tuple, ...],
                    atomic_paths: tuple[tuple, ...] = ()) -> dict:
    atomic_paths = _validated_atomic_paths(candidate, paths, atomic_paths)
    expected = {'patches'} | ({'appends'} if append_paths else set()) | ({'removes'} if remove_paths else set())
    if set(value) != expected:
        raise ValueError('repair只接受授权patches、显式appends与removes，不接受全量JSON')
    result = _patch(candidate, {'patches': value['patches']}, paths)
    _frozen_list_layout(candidate, result, atomic_paths=atomic_paths)
    if remove_paths:
        if not isinstance(value['removes'], list) or len(value['removes']) != len(remove_paths):
            raise ValueError('removes必须恰好覆盖每个授权条目')
        seen = set()
        for item in value['removes']:
            if not isinstance(item, dict) or set(item) != {'path'}:
                raise ValueError('remove只接受path，不接受替代值')
            path = _json_path(item['path'])
            if path not in remove_paths or path in seen:
                raise ValueError('remove越界或重复')
            seen.add(path)
            parent = _at(result, path[:-1])
            if not isinstance(parent, dict) or not isinstance(path[-1], str) or path[-1] not in parent:
                raise ValueError('remove只能删除明确既有字典语义键，不得压缩列表位置')
            del parent[path[-1]]
    if append_paths:
        if not isinstance(value['appends'], list) or len(value['appends']) != len(append_paths):
            raise ValueError('appends必须恰好覆盖每个授权容器')
        seen = set()
        for item in value['appends']:
            if not isinstance(item, dict) or set(item) != {'path', 'value'}:
                raise ValueError('append只接受path与value')
            path = _json_path(item['path'])
            if path not in append_paths or path in seen:
                raise ValueError('append越界或重复')
            seen.add(path)
            parent = _at(result, path)
            if not item['value']:
                raise ValueError('显式append必须实际增加条目；不能用空值冒充修复')
            if isinstance(parent, list) and isinstance(item['value'], list):
                parent.extend(item['value'])
            elif isinstance(parent, dict) and isinstance(item['value'], dict):
                if parent.keys() & item['value'].keys():
                    raise ValueError('append不得覆盖既有语义键')
                parent.update(item['value'])
            else:
                raise ValueError('append类型必须与授权容器一致')
    return result


def strict_json(text: str) -> dict:
    """仅解析严格 JSON；不猜补引号，不接受 Python 扩展的非有限常量。"""
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'JSON 重复键：{key}')
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError(f'JSON 不允许非有限常量：{value}')
    value = json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError('语义回答须是 JSON 对象')
    return value


def _material(index: SourceIndex, query: dict, max_chapter: int | None,
              material_candidates: tuple[str, ...] | None = None, *,
              reference_mode: Literal['chapter_numbers', 'canonical'] = 'canonical') -> dict:
    if not isinstance(query, dict):
        raise ValueError('材料查询须为对象')
    candidate_key = None
    if material_candidates is not None:
        candidate_key = query.get('candidate_key')
        if not isinstance(candidate_key, str) or candidate_key not in material_candidates:
            raise ValueError('关联补料必须绑定一个已给候选candidate_key')
        query = {key: value for key, value in query.items() if key != 'candidate_key'}
    if reference_mode not in ('chapter_numbers', 'canonical'):
        raise ValueError('材料引用协议未登记')
    if reference_mode == 'chapter_numbers' and material_candidates is not None:
        raise ValueError('章节整数补料不能授予关联候选权限')
    actual_query = query
    if reference_mode == 'chapter_numbers' and query.get('operation') == 'read_segments':
        numbers = query.get('segment_ids')
        if (not isinstance(numbers, list) or not 1 <= len(numbers) <= 40
                or any(type(number) is not int or number <= 0 or number not in index.evidence_ids
                       for number in numbers)):
            raise ValueError('章节补料段引用须为已给的严格正整数，不接受字符串、布尔或浮点数')
        actual_query = {**query, 'segment_ids': [index.evidence_ids[number] for number in numbers]}
    result = _query_material(index, actual_query, max_chapter)
    if reference_mode == 'chapter_numbers':
        shown_query = dict(result['query'])
        if shown_query.get('operation') == 'read_segments':
            shown_query['segment_ids'] = [index.evidence_numbers[ref] for ref in shown_query['segment_ids']]
        result = {**result, 'query': shown_query,
            'segments': [{'ref': index.evidence_numbers[ref], **segment}
                         for ref, segment in result['segments'].items()]}
    result['reference_mode'] = reference_mode
    return {**result, 'candidate_key': candidate_key} if material_candidates is not None else result


def _query_material(index: SourceIndex, query: dict, max_chapter: int | None) -> dict:
    operation = query.get('operation')
    if operation == 'read_segments':
        if set(query) - {'operation', 'segment_ids', 'adjacent_paragraphs'}:
            raise ValueError('段读取包含未登记字段')
        if not 0 <= query.get('adjacent_paragraphs', 0) <= 3:
            raise ValueError('单次邻段范围须为 0..3')
        if not 1 <= len(query['segment_ids']) <= 40:
            raise ValueError('单次精确读取段数须为 1..40')
        return read_segments(index, query['segment_ids'],
                             adjacent_paragraphs=query.get('adjacent_paragraphs', 0),
                             max_chapter=max_chapter).material()
    if operation == 'search_literal':
        if set(query) - {'operation', 'terms', 'mode', 'chapter_range', 'cursor', 'limit'}:
            raise ValueError('字面查询包含未登记字段')
        bounds = tuple(query.get('chapter_range', (0, max_chapter or len(index.chapter_ordinals))))
        if len(bounds) != 2 or (max_chapter is not None and bounds[1] > max_chapter):
            raise ValueError('查询越出本 owner 的源章范围')
        if not 1 <= query.get('limit', 20) <= 40:
            raise ValueError('查询每页须为 1..40 段')
        return search_literal(index, query['terms'], mode=query.get('mode', 'all'),
                              chapter_range=bounds, cursor=query.get('cursor', 0),
                              limit=query.get('limit', 20)).material()
    raise ValueError('未登记的材料操作')


def _material_trace_results(step: dict, *, index: SourceIndex | None,
                            max_chapter: int | None,
                            material_candidates: tuple[str, ...] | None,
                            owner: str | None = None) -> tuple[dict, ...]:
    control, frozen = step['value'], step['paths']
    actual_owner = step['ref']['owner']
    if owner is not None and actual_owner != owner:
        raise ValueError('材料trace的真实owner与当前任务不同')
    reference_mode = material_reference_mode(actual_owner)
    if (not isinstance(control, dict) or set(control) != {'kind', 'requests'}
            or control['kind'] != 'need_materials' or not isinstance(control['requests'], list)
            or not 1 <= len(control['requests']) <= 8 or not isinstance(frozen, dict)
            or set(frozen) != {'source_identity', 'content_sha256', 'max_chapter', 'reference_mode',
                               'materials', 'materials_sha256'}
            or frozen['reference_mode'] != reference_mode
            or not isinstance(frozen['materials'], list)
            or any(not isinstance(item, dict) or item.get('reference_mode') != reference_mode
                   for item in frozen['materials'])
            or digest(frozen['materials']) != frozen['materials_sha256']):
        raise ValueError('材料trace没有完整控制回答、源绑定或结果摘要')
    if index is not None:
        actual = [_material(index, query, max_chapter, material_candidates, reference_mode=reference_mode)
                  for query in control['requests']]
        if (frozen['source_identity'] != index.source_identity
                or frozen['content_sha256'] != index.content_sha256
                or frozen['max_chapter'] != max_chapter or digest(frozen['materials']) != digest(actual)):
            raise ValueError('材料trace与当前源索引重算结果或候选权限不一致')
    # 无索引时只重建业务JSON；真实来源证明由持有SourceIndex的耐久边界完成。
    return tuple(actual if index is not None else frozen['materials'])


def replay_material_trace(trace: tuple[dict, ...] | list[dict], *, index: SourceIndex | None,
                          max_chapter: int | None = None,
                          material_candidates: tuple[str, ...] | None = None,
                          owner: str | None = None) -> tuple[dict, ...]:
    """从原始控制回答重跑查询，不把runtime自报的材料摘要当证据权限。"""
    materials, pending_delivery, pending_owner = [], False, None
    for step in trace:
        actual_owner = step['ref'].get('owner')
        if owner is not None and actual_owner != owner:
            raise ValueError('材料trace的送达回答与当前真实owner不同')
        if pending_delivery:
            if actual_owner != pending_owner:
                raise ValueError('冻结补料未送达原请求owner')
            user = step['ref'].get('prompts', {}).get('user', '')
            payload = json.dumps(materials, ensure_ascii=False)
            if not any(user.endswith(marker + payload)
                       for marker in ('\n【已冻结补充材料】\n', '\n【冻结补充材料】\n')):
                raise ValueError('材料trace没有绑定真正收到冻结补料的后续请求')
            pending_delivery = False
        if step['kind'] == 'changes':
            materials.extend(replay_material_trace(step['value'], index=index, max_chapter=max_chapter,
                material_candidates=material_candidates, owner=owner))
        elif step['kind'] == 'material':
            materials.extend(_material_trace_results(step, index=index, max_chapter=max_chapter,
                material_candidates=material_candidates, owner=owner))
            pending_delivery = True
            pending_owner = actual_owner
    if pending_delivery:
        raise ValueError('材料trace尚无实际送达补料的后续回答')
    return tuple(materials)


def _diagnostic_details(exc: Exception) -> list[dict]:
    """保留每条原始规则、位置和文案，避免批量窄修把错误与条目拆散。"""
    if isinstance(exc, ValidationError):
        return [{'code': row['type'], 'path': tuple(row['loc']), 'message': row['msg']}
                for row in exc.errors()]
    if getattr(exc, 'issues', None):
        return [{'code': getattr(issue, 'code', type(issue).__name__), 'path': tuple(issue.path),
                 'message': issue.message, 'owner': getattr(issue, 'owner', ''),
                 'item': getattr(issue, 'item', '')} for issue in exc.issues]
    return [{'code': getattr(exc, 'code', type(exc).__name__), 'path': (), 'message': str(exc)}]


def _diagnose(exc: Exception, candidate: dict) -> tuple[list[str], tuple[tuple, ...]]:
    errors = _diagnostic_details(exc)
    paths = []
    for error in errors:
        location = error['path']
        cursor: Any = candidate
        path = []
        for position, component in enumerate(location):
            if type(component) not in (str, int) or type(component) is int and component < 0:
                path = []
                break
            if not isinstance(cursor, (dict, list, tuple)):
                path = []
                break
            if isinstance(cursor, dict):
                if type(component) is not str:
                    path = []
                    break
                if component not in cursor:
                    # 缺失固定字段允许补齐；不能把尚未存在的中间容器猜成授权条目。
                    path = [*path, component] if position == len(location) - 1 else []
                    break
            if isinstance(cursor, (list, tuple)) and (type(component) is not int
                                                      or not 0 <= component < len(cursor)):
                path = []
                break
            path.append(component)
            cursor = cursor[component]
            if isinstance(component, int):
                break
        if path:
            paths.append(tuple(path))
        else:
            # 部分错误无法安全定位时，不只修其它条目并隐瞒范围不足。
            return [error['message'] for error in errors], ()
    minimal = []
    for path in sorted(set(paths), key=lambda p: (len(p), str(p))):
        if not any(path[:len(existing)] == existing for existing in minimal):
            minimal.append(path)
    return [error['message'] for error in errors], tuple(minimal)


def _defect_identity(exc: Exception, paths: tuple[tuple, ...]) -> frozenset[tuple]:
    """缺陷身份只取typed规则与定位；文案、坏值与出现次数不构成进展。"""
    if isinstance(exc, ValidationError):
        return frozenset((row['type'], '', '', tuple(row['loc'])) for row in exc.errors())
    if getattr(exc, 'issues', None):
        return frozenset((getattr(row, 'code', type(row).__name__), getattr(row, 'owner', ''),
                          getattr(row, 'item', ''), tuple(row.path)) for row in exc.issues)
    return frozenset((getattr(exc, 'code', type(exc).__name__), '', '', path) for path in (paths or ((),)))


def _patch(candidate: dict, value: dict, paths: tuple[tuple, ...]) -> dict:
    import copy
    if set(value) != {'patches'} or not isinstance(value['patches'], list):
        raise ValueError('窄修仅接受 patches 数组')
    if len(value['patches']) != len(paths):
        raise ValueError('补丁须恰好覆盖每个授权条目')
    result = copy.deepcopy(candidate)
    seen = set()
    for item in value['patches']:
        if not isinstance(item, dict) or set(item) != {'path', 'value'}:
            raise ValueError('补丁仅接受 path 与 value')
        path = _json_path(item['path'])
        if path not in paths or path in seen:
            raise ValueError('补丁越界或重复')
        seen.add(path)
        target = _at(result, path[:-1])
        if isinstance(target, dict) and type(path[-1]) is not str:
            raise ValueError('对象路径须使用字符串键')
        if isinstance(target, list) and type(path[-1]) is not int:
            raise ValueError('数组路径须使用整数索引')
        target[path[-1]] = item['value']
    return result


def _changes(candidate: dict, value: dict) -> dict:
    """重放语义owner已验收的显式set/remove；不推断权限或领域事实。"""
    import copy
    result = copy.deepcopy(candidate)
    removals, appends, seen = [], [], set()
    for change in value['changes']:
        path = tuple(change['path'])
        if not path or path in seen or any(type(part) not in (str, int) for part in path):
            raise ValueError('changes恢复路径为空、重复或含非法索引')
        seen.add(path)
        parent, original = _at(result, path[:-1]), _at(candidate, path[:-1])
        key, operation = path[-1], change['operation']
        if operation not in ('set', 'remove'):
            raise ValueError('changes恢复只接受显式set/remove')
        if isinstance(parent, dict) and isinstance(key, str):
            if operation == 'remove':
                del parent[key]
            else:
                parent[key] = copy.deepcopy(change['value'])
        elif isinstance(parent, list) and type(key) is int and key >= 0:
            if key < len(original):
                if operation == 'remove':
                    removals.append((path[:-1], key))
                else:
                    parent[key] = copy.deepcopy(change['value'])
            elif operation == 'set':
                appends.append((path[:-1], key, copy.deepcopy(change['value'])))
            else:
                raise ValueError('changes不能删除不存在的列表位置')
        else:
            raise ValueError('changes恢复的父容器或键不匹配')
    for path, key in sorted(removals, key=lambda row: (str(row[0]), -row[1])):
        del _at(result, path)[key]
    counts = {}
    for path, key, item in sorted(appends, key=lambda row: (str(row[0]), row[1])):
        if key != len(_at(candidate, path)) + counts.get(path, 0):
            raise ValueError('changes恢复只能在原列表尾部连续追加')
        _at(result, path).append(item)
        counts[path] = counts.get(path, 0) + 1
    return result


def replay_semantic_trace(trace: tuple[dict, ...] | list[dict], *, index: SourceIndex | None = None,
                          max_chapter: int | None = None,
                          material_candidates: tuple[str, ...] | None = None,
                          owner: str | None = None) -> dict:
    """从已核验原响应逐步重建候选；不是从最后patch猜完整业务。"""
    replay_material_trace(trace, index=index, max_chapter=max_chapter,
                          material_candidates=material_candidates, owner=owner)
    candidate = None
    for step in trace:
        if set(step) != {'kind', 'value', 'paths', 'ref'}:
            raise ValueError('语义恢复trace字段不完整')
        if step['kind'] == 'material':
            _material_trace_results(step, index=index, max_chapter=max_chapter,
                                    material_candidates=material_candidates, owner=owner)
        elif step['kind'] == 'result' and candidate is None and not step['paths']:
            candidate = step['value']
        elif step['kind'] == 'patch' and candidate is not None:
            candidate = _patch(candidate, step['value'], tuple(tuple(path) for path in step['paths']))
        elif step['kind'] == 'repair' and candidate is not None:
            if set(step['paths']) != {'replace', 'append', 'remove', 'atomic'}:
                raise ValueError('repair恢复缺少实际replace/append/remove/atomic能力')
            candidate = _revision_patch(candidate, step['value'],
                tuple(tuple(path) for path in step['paths']['replace']),
                tuple(tuple(path) for path in step['paths']['append']),
                tuple(tuple(path) for path in step['paths']['remove']),
                tuple(tuple(path) for path in step['paths']['atomic']))
        elif step['kind'] == 'changes' and candidate is not None:
            if not isinstance(step['value'], list) or step['paths']:
                raise ValueError('组合changes须携带原始完整语义trace')
            if not step['value'] or step['ref'] != step['value'][-1]['ref']:
                raise ValueError('组合changes末响应身份不匹配')
            candidate = _changes(candidate, replay_semantic_trace(step['value'], index=index,
                max_chapter=max_chapter, material_candidates=material_candidates, owner=owner))
        else:
            raise ValueError('语义恢复trace顺序或分支非法')
    if not isinstance(candidate, dict):
        raise ValueError('缺少可重建的语义候选')
    return candidate


def _response_kind(owner: str, value: dict | None, patch_paths: tuple[tuple, ...]) -> str:
    if value is None:
        return 'format_failure'
    if value.get('kind') == 'needs_design':
        return 'upstream_issue'
    explicit_repair = ((owner == 'book' and value.get('kind') == 'book_repair')
                       or (owner.startswith('body_ch') and value.get('kind') in ('patch', 'needs_scope')))
    return 'patch' if patch_paths or explicit_repair else 'result'


@dataclass(frozen=True)
class _SchemaIssue:
    code: str
    path: tuple
    message: str
    owner: str = ''
    item: str = ''


class _CandidateSchemaError(ValueError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__('；'.join(issue.message for issue in self.issues))


def _schema_error_paths(error) -> tuple[tuple, ...]:
    """JSON Schema的对象级缺字段/多字段规则按原始结构精确定位，不解析英文报错。"""
    path = tuple(error.absolute_path)
    if error.validator == 'required' and isinstance(error.instance, dict):
        return tuple(path + (field,) for field in error.validator_value if field not in error.instance)
    if error.validator == 'additionalProperties' and error.validator_value is False and isinstance(error.instance, dict):
        import re
        declared = error.schema.get('properties', {})
        patterns = error.schema.get('patternProperties', {})
        return tuple(path + (field,) for field in error.instance
                     if field not in declared and not any(re.search(pattern, field) for pattern in patterns))
    return (path,)


def _compile_candidate(value: dict, compiler: Callable[[dict], Any], *, owner: str,
                       schema: dict | None) -> None:
    """动态schema属于实际生成输入；普通局部诊断同轮聚合，跨owner控制异常原样上抛。"""
    if schema is None:
        compiler(value)
        return
    from jsonschema import Draft202012Validator
    issues = []
    try:
        compiler(value)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        node = semantic_owner_unit(owner)[0]
        if (isinstance(exc, ValidationStop) or getattr(exc, 'node', node) != node
                or hasattr(exc, 'problems') or getattr(exc, 'source_issues', None)
                or not isinstance(exc, ValidationError) and not getattr(exc, 'issues', None)):
            raise
        issues.extend(_SchemaIssue(**row) for row in _diagnostic_details(exc))
    for error in sorted(Draft202012Validator(schema).iter_errors(value),
                        key=lambda row: (str(tuple(row.absolute_path)), row.message)):
        issues.extend(_SchemaIssue('response_schema', path, error.message, owner)
                      for path in _schema_error_paths(error))
    if issues:
        raise _CandidateSchemaError(issues)


def _schema_projection(schema: dict, path: tuple, *, root: dict | None = None) -> dict:
    """只投影明确JSON Schema结构，不从候选坏值或自然语言推断字段类型。"""
    from copy import deepcopy
    root = schema if root is None else root
    if not path:
        return deepcopy(schema)
    if '$ref' in schema:
        reference = schema['$ref']
        if not isinstance(reference, str) or not reference.startswith('#/'):
            raise ValueError('补丁schema只接受当前完整schema内的本地引用')
        target: Any = root
        for component in reference[2:].split('/'):
            target = target[component.replace('~1', '/').replace('~0', '~')]
        if not isinstance(target, dict):
            raise ValueError('补丁schema引用不指向结构')
        siblings = {key: child for key, child in schema.items() if key != '$ref'}
        if set(siblings) - {'title', 'description', 'default', 'examples'}:
            return _schema_projection({'allOf': [target, siblings]}, path, root=root)
        return _schema_projection(target, path, root=root)
    for combinator in ('anyOf', 'oneOf', 'allOf'):
        if combinator not in schema:
            continue
        alternatives = []
        for branch in schema[combinator]:
            try:
                alternatives.append(_schema_projection(branch, path, root=root))
            except ValueError:
                continue
        if not alternatives:
            raise ValueError('授权路径不属于完整候选schema')
        # 不同候选分支可能投影为同一局部类型，不能用oneOf错误地排斥重叠结果。
        result = {'allOf' if combinator == 'allOf' else 'anyOf': alternatives}
        siblings = {key: child for key, child in schema.items() if key != combinator}
        if 'properties' in siblings or 'items' in siblings or 'prefixItems' in siblings or '$ref' in siblings:
            return {'allOf': [result, _schema_projection(siblings, path, root=root)]}
        return result
    component, tail = path[0], path[1:]
    child = None
    if isinstance(component, str):
        child = schema.get('properties', {}).get(component)
        if child is None and isinstance(schema.get('additionalProperties'), dict):
            child = schema['additionalProperties']
    elif type(component) is int and component >= 0:
        prefix = schema.get('prefixItems', [])
        child = prefix[component] if component < len(prefix) else schema.get('items')
    if not isinstance(child, dict):
        raise ValueError('授权路径没有明确的局部value schema')
    return _schema_projection(child, tail, root=root)


def _patch_response_schema(schema: dict, paths: tuple[tuple, ...]) -> dict:
    """完整候选与补丁回答是两个schema；path常量与每项value约束成对绑定。"""
    from copy import deepcopy
    definitions, variants, value_refs = deepcopy(schema.get('$defs', {})), [], {}
    for number, path in enumerate(paths):
        value_schema = _schema_projection(schema, path)
        identity = digest(value_schema)
        if identity not in value_refs:
            value_key = f'__repair_value_{len(value_refs)}'
            while value_key in definitions:
                value_key += '_'
            definitions[value_key] = value_schema
            value_refs[identity] = {'$ref': '#/$defs/' + value_key}
        key = f'__repair_patch_{number}'
        while key in definitions:
            key += '_'
        definitions[key] = {'type': 'object', 'additionalProperties': False, 'required': ['path', 'value'],
            'properties': {'path': {'type': 'array', 'const': list(path)},
                           'value': value_refs[identity]}}
        variants.append({'$ref': '#/$defs/' + key})
    return {'type': 'object', 'additionalProperties': False, 'required': ['patches'],
        '$defs': definitions,
        'properties': {'patches': {'type': 'array', 'minItems': len(paths), 'maxItems': len(paths),
            'items': {'oneOf': variants},
            'allOf': [{'contains': variant, 'minContains': 1, 'maxContains': 1} for variant in variants]}}}


def _format_repair_user(base_user: str, errors: list[str], raw: str) -> str:
    """发送与来源认证共用格式修复正文；补料仍由调用方按真实送达单独绑定。"""
    return (base_user + '\n【格式修复】上轮未能解码为唯一键JSON对象：' + '；'.join(errors)
        + '\n仅修正JSON语法并完整提交符合原schema的JSON；保留原回答已表达的主体与事实，'
        + '不得借格式修复重写任务、扩展scope或猜补故事事实。代码不会自动补引号选择含义。'
        + '\n【格式修复原始回答】\n' + json.dumps({'raw_response': raw}, ensure_ascii=False))


def _automatic_repair_user(base_user: str, context: dict, *, schema: dict | None, owner: str) -> str:
    user = (base_user + '\n【冻结候选与窄修权限】\n'
        + json.dumps(context, ensure_ascii=False)
        + '\n仅返回 {"patches":[{"path":[给定路径],"value":完整替换条目}]}。'
        + '每个授权路径恰答一次；替换后的完整候选仍须符合原任务schema、引用和证据约束。'
        + '逐条修复全部diagnostics，不得只修第一条；不得改其他条目，不得删除未授权条目或重排既有数组。')
    if schema is not None:
        try:
            response_schema = _patch_response_schema(schema, tuple(context['allowed_paths']))
        except (ValueError, KeyError, IndexError) as exc:
            node, item = semantic_owner_unit(owner)
            raise ValidationStop('完整候选schema不能授权这些局部替换，须显式处理范围：' + str(exc),
                code='repair_schema_scope', node=node, stage=node.split('.')[1], item=item,
                phase='accept', context=context) from exc
        user += ('\n【本轮补丁回答 schema】'
            + json.dumps(response_schema, ensure_ascii=False)
            + '\n原任务的输出schema约束完整候选；本轮只按补丁回答schema提交patches，不重新返回完整候选。')
    return user


def request_json(session: PaidRequestSession, owner: str, request: ChatRequest,
                 compile_value: Callable[[dict], Any], *, repository=None,
                 index: SourceIndex | None = None, max_chapter: int | None = None,
                 revision_seed: RevisionSeed | None = None,
                 material_candidates: tuple[str, ...] | None = None,
                 compile_with_materials: Callable[[dict, tuple[dict, ...]], Any] | None = None,
                 material_prompt: Callable[[tuple[dict, ...]], str] | None = None,
                 candidate_schema: Callable[[tuple[dict, ...]], dict] | None = None) -> SemanticResult:
    """每个回答先耐久；材料续答与窄修均使用原 owner 的持久额度。"""
    refs = list(revision_seed.refs) if revision_seed is not None else []
    seen = set()
    candidate = revision_seed.baseline if revision_seed is not None else None
    patch_paths: tuple[tuple, ...] = revision_seed.paths if revision_seed is not None else ()
    append_paths = revision_seed.append_paths if revision_seed is not None else ()
    remove_paths = revision_seed.remove_paths if revision_seed is not None else ()
    atomic_paths = revision_seed.atomic_paths if revision_seed is not None else ()
    material_cache = (list(replay_material_trace(revision_seed.trace, index=index, max_chapter=max_chapter,
        material_candidates=material_candidates, owner=owner)) if revision_seed is not None and index is not None else [])
    reference_mode = material_reference_mode(owner)
    trace: list[dict] = list(revision_seed.trace) if revision_seed is not None else []
    previous_defects: frozenset[tuple] | None = None
    base_user = request.user
    continuation_user = base_user
    automatic_repair = None
    while True:
        enforce_request_budget([request], node=semantic_owner_unit(owner)[0], purpose=owner)
        answer = session.send(owner, request)
        value = None
        merged = None
        raw = answer.content if (answer.content or '').strip() else (answer.reasoning or '')
        try:
            value = strict_json(raw)
            if value.get('kind') == 'need_materials':
                if index is None or set(value) != {'kind', 'requests'}:
                    raise ValueError('本任务不接受材料请求或请求字段非法')
                queries = value['requests']
                if not isinstance(queries, list) or not 1 <= len(queries) <= 8:
                    raise ValueError('每轮材料请求须为 1..8 个查询')
                materials = [_material(index, query, max_chapter, material_candidates, reference_mode=reference_mode)
                             for query in queries]
                identity = digest(materials)
                if identity in seen:
                    raise ValueError('重复材料请求无进展')
                seen.add(identity)
                if repository is not None:
                    repository.write(f'runtime/materials/{identity}.json', {
                        'contract_version': STORY_VERSION, 'owner': owner,
                        'request_sha256': answer.request_sha256,
                        'source_identity': index.source_identity, 'reference_mode': reference_mode,
                        'materials': materials})
                session.record_acceptance(answer, 'json', parse='ok', accepted=True,
                                          response_kind='material',
                                          salvage='reasoning' if not (answer.content or '').strip() else 'none')
                refs.append(answer.ref)
                material_cache.extend(materials)
                trace.append({'kind': 'material', 'value': value, 'paths': {
                    'source_identity': index.source_identity, 'content_sha256': index.content_sha256,
                    'max_chapter': max_chapter, 'reference_mode': reference_mode,
                    'materials': materials, 'materials_sha256': identity},
                    'ref': dict(answer.ref._asdict())})
                if material_prompt is not None:
                    updated_user = material_prompt(tuple(material_cache))
                    continuation_user = updated_user + continuation_user[len(base_user):]
                    base_user = updated_user
                if automatic_repair is not None:
                    continuation_user = _automatic_repair_user(base_user, automatic_repair,
                        schema=candidate_schema(tuple(material_cache)) if candidate_schema is not None else None,
                        owner=owner)
                # 材料只补充事实输入；不能抹掉本轮已经冻结的候选、错误和补丁协议。
                request = replace(request, user=continuation_user + '\n【已冻结补充材料】\n'
                                  + json.dumps(material_cache, ensure_ascii=False))
                continue
            merged = (_revision_patch(candidate, value, patch_paths, append_paths, remove_paths, atomic_paths)
                if revision_seed is not None else _patch(candidate, value, patch_paths) if patch_paths else value)
            if revision_seed is not None and merged == revision_seed.baseline:
                raise ValidationStop('回派补丁没有改变任何授权条目', code='owner_revision_no_progress',
                    node=semantic_owner_unit(owner)[0], stage=semantic_owner_unit(owner)[0].split('.')[1],
                    item=semantic_owner_unit(owner)[1], phase='accept')
            trace.append({'kind': 'repair' if revision_seed is not None else 'patch' if patch_paths else 'result', 'value': value,
                          'paths': {'replace': [list(path) for path in patch_paths],
                                    'append': [list(path) for path in append_paths],
                                    'remove': [list(path) for path in remove_paths],
                                    'atomic': [list(path) for path in atomic_paths]}
                              if revision_seed is not None else [list(path) for path in patch_paths],
                          'ref': dict(answer.ref._asdict())})
            compiler = (lambda current: compile_with_materials(current, tuple(material_cache))
                        if compile_with_materials is not None else compile_value(current))
            _compile_candidate(merged, compiler, owner=owner,
                schema=candidate_schema(tuple(material_cache)) if candidate_schema is not None else None)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            current = merged if isinstance(merged, dict) else (candidate if revision_seed is not None or patch_paths else value)
            errors, paths = _diagnose(exc, current if isinstance(current, dict) else {})
            diagnostics = _diagnostic_details(exc)
            empty = not (answer.content or '').strip()
            session.record_acceptance(answer, 'json', parse='empty' if empty else 'failed',
                errors=errors, salvage='reasoning' if empty and answer.reasoning else 'none', accepted=False,
                response_kind='patch' if revision_seed is not None else _response_kind(owner, value, patch_paths))
            defects = _defect_identity(exc, paths)
            signature = digest(sorted(defects, key=str))
            node, item = semantic_owner_unit(owner)
            if (getattr(exc, 'node', node) != node or hasattr(exc, 'problems')
                    or getattr(exc, 'source_issues', None)):
                exc.semantic_result = SemanticResult(current, tuple((*refs, answer.ref)), answer, tuple(trace),
                                                     tuple(material_cache))
                if owner == 'book' and getattr(exc, 'source_issues', None) and repository is not None:
                    repository.write('runtime/source_repairs/book_candidate.json', {
                        'contract_version': STORY_VERSION, 'request_sha256': answer.request_sha256,
                        'wire': getattr(exc, 'book_wire', current), 'trace': trace,
                        'refs': [dict(ref._asdict()) for ref in exc.semantic_result.refs]})
                raise
            if isinstance(exc, ValidationStop):
                raise
            if empty:
                # 空答案未交付可用语义，reasoning 未过同一 compiler 也不能占据
                # 请求身份。保持原请求，在持久的同轮 attempt 额度内重发。
                continue
            if signature in seen or previous_defects is not None and previous_defects <= defects:
                raise ValidationStop('语义候选无进展：' + '；'.join(errors), code='story_no_progress',
                    node=node, stage=node.split('.')[1], item=item, phase='accept',
                    context={'errors': errors, 'paths': paths, 'diagnostics': diagnostics}) from exc
            previous_defects = defects
            if not paths and not isinstance(current, dict):
                seen.add(signature)
                continuation_user = _format_repair_user(base_user, errors, raw)
                # 新请求是无状态调用：格式修复同样必须再次送达已冻结材料。
                # continuation_user只保留控制正文，避免后续补料时重复叠加旧材料块。
                request = replace(request, user=continuation_user + (
                    '\n【已冻结补充材料】\n' + json.dumps(material_cache, ensure_ascii=False)
                    if material_cache else ''))
                continue
            if not paths:
                raise ValidationStop('语义候选未通过：' + '；'.join(errors), code='story_candidate',
                                     node=node, stage=node.split('.')[1], item=item, phase='accept',
                                     context={'errors': errors, 'paths': paths, 'diagnostics': diagnostics}) from exc
            if revision_seed is not None:
                # 数组普通窄修按条目授权；显式回派仍按原始精确诊断核对每一处，
                # 不能因同一数组条目有一个获准字段，就忽略另一个授权外缺陷。
                locations = tuple(error['path'] for error in diagnostics)
                bounded = tuple(path for path in revision_seed.paths
                    if any(path[:len(found)] == found or found[:len(path)] == path for found in locations))
                if (not bounded or any(not found or not any(
                        path[:len(found)] == found or found[:len(path)] == path
                        for path in revision_seed.paths) for found in locations)):
                    raise ValidationStop('修订需要授权外位置，须显式扩大范围', code='revision_scope_required',
                        node=node, stage=node.split('.')[1], item=item, phase='accept',
                        context={'errors': errors, 'paths': paths, 'diagnostics': diagnostics}) from exc
                paths = bounded
            seen.add(signature)
            candidate = current
            patch_paths = paths
            atomic_paths = (tuple(path for path in revision_seed.atomic_paths if path in patch_paths)
                            if revision_seed is not None else ())
            if merged is not None:
                # 已合并的结构操作在trace中保留；后续字段窄修不得重复追加或删除。
                append_paths, remove_paths = (), ()
            refs.append(answer.ref)
            continuation_user = (base_user + '\n【冻结候选与窄修权限】\n'
                + json.dumps({'candidate': candidate, 'errors': errors,
                              'diagnostics': diagnostics, 'allowed_paths': paths,
                              **({'atomic_paths': atomic_paths} if revision_seed is not None else {})}, ensure_ascii=False)
                + '\n仅返回 {"patches":[{"path":[给定路径],"value":完整替换条目}]}'
                + ('，另必填appends（只可使用原授权容器）。' if append_paths else '。')
                + ('另必填removes（只可删除原授权字典语义键）。' if remove_paths else '')
                + '每个授权路径恰答一次；替换后的完整候选仍须符合原任务schema、引用和证据约束。'
                + ('只有atomic_paths中的精确语义字段可以整体重分段；其他数组位置冻结。' if atomic_paths else '')
                + '逐条修复全部diagnostics，不得只修第一条；不得改其他条目，不得删除未授权条目或重排既有数组。')
            if revision_seed is None:
                automatic_repair = {'candidate': candidate, 'errors': errors,
                    'diagnostics': diagnostics, 'allowed_paths': paths}
                continuation_user = _automatic_repair_user(base_user, automatic_repair,
                    schema=candidate_schema(tuple(material_cache)) if candidate_schema is not None else None,
                    owner=owner)
            request = replace(request, user=continuation_user + '\n【冻结补充材料】\n'
                + json.dumps(material_cache, ensure_ascii=False))
            continue
        session.record_acceptance(answer, 'json', parse='ok', accepted=True,
            salvage='reasoning' if not (answer.content or '').strip() else 'none',
            response_kind='patch' if revision_seed is not None else _response_kind(owner, value, patch_paths))
        refs.append(answer.ref)
        return SemanticResult(merged, tuple(refs), answer, tuple(trace), tuple(material_cache))
