"""当前故事契约的耐久语义请求、材料补全与受限 JSON 补丁。

实际外发、授权、额度及账仍只有 PaidRequestSession 一个边界。本组合层不解读
小说；只执行显式材料查询和已定位字段替换，任何候选均重过同一 compiler。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

from pydantic import ValidationError

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


@dataclass(frozen=True)
class SemanticResult:
    value: dict
    refs: tuple
    answer: Any
    trace: tuple[dict, ...] = ()


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

    def __post_init__(self):
        if self.kind != 'repair' or not isinstance(self.baseline, dict):
            raise ValueError('修订必须有明确 repair 种类及对象基线')
        if not self.paths and not self.append_paths and not self.remove_paths:
            raise ValueError('修订没有明确授权位置，范围不足须停止')
        if not self.refs or replay_semantic_trace(self.trace) != self.baseline:
            raise ValueError('修订基线不能从真实回答trace完整重建')
        all_paths = (*self.paths, *self.append_paths, *self.remove_paths)
        if len(set(all_paths)) != len(all_paths) or any(not path for path in all_paths):
            raise ValueError('修订路径不得为空或重复')
        for path in self.paths:
            value = _at(self.baseline, path)
            if isinstance(value, (list, tuple)):
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


def _at(value: Any, path: tuple):
    for component in path:
        if isinstance(component, bool):
            raise ValueError('路径索引不接受布尔值')
        value = value[component]
    return value


def _frozen_list_layout(before: Any, after: Any) -> None:
    """replace不能增删或重排已有数组；新增必须走单独的append授权。"""
    if isinstance(before, (list, tuple)):
        if not isinstance(after, (list, tuple)) or len(before) != len(after):
            raise ValueError('替换不得改变既有数组长度；结构新增须显式append')
        if list(before) != list(after) and sorted(map(digest, before)) == sorted(map(digest, after)):
            raise ValueError('不得重排既有列表位置')
        for old, new in zip(before, after):
            _frozen_list_layout(old, new)
    elif isinstance(before, dict) and isinstance(after, dict):
        for key in before.keys() & after.keys():
            _frozen_list_layout(before[key], after[key])


def _revision_patch(candidate: dict, value: dict, paths: tuple[tuple, ...],
                    append_paths: tuple[tuple, ...], remove_paths: tuple[tuple, ...]) -> dict:
    expected = {'patches'} | ({'appends'} if append_paths else set()) | ({'removes'} if remove_paths else set())
    if set(value) != expected:
        raise ValueError('repair只接受授权patches、显式appends与removes，不接受全量JSON')
    result = _patch(candidate, {'patches': value['patches']}, paths)
    _frozen_list_layout(candidate, result)
    if remove_paths:
        if not isinstance(value['removes'], list) or len(value['removes']) != len(remove_paths):
            raise ValueError('removes必须恰好覆盖每个授权条目')
        seen = set()
        for item in value['removes']:
            if not isinstance(item, dict) or set(item) != {'path'}:
                raise ValueError('remove只接受path，不接受替代值')
            path = tuple(item['path'])
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
            if set(item) != {'path', 'value'}:
                raise ValueError('append只接受path与value')
            path = tuple(item['path'])
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
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'JSON 重复键：{key}')
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('语义回答须是 JSON 对象')
    return value


def _material(index: SourceIndex, query: dict, max_chapter: int | None) -> dict:
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


def _diagnose(exc: Exception, candidate: dict) -> tuple[list[str], tuple[tuple, ...]]:
    if isinstance(exc, ValidationError):
        errors = [(tuple(row['loc']), row['msg']) for row in exc.errors()]
    elif getattr(exc, 'issues', None):
        errors = [(tuple(issue.path), issue.message) for issue in exc.issues]
    else:
        return [str(exc)], ()
    paths = []
    for location, _message in errors:
        cursor: Any = candidate
        path = []
        for component in location:
            if not isinstance(cursor, (dict, list, tuple)):
                break
            if isinstance(cursor, dict) and component not in cursor:
                path.append(component)
                break
            if isinstance(cursor, (list, tuple)) and (not isinstance(component, int)
                                                      or not 0 <= component < len(cursor)):
                break
            path.append(component)
            cursor = cursor[component]
            if isinstance(component, int):
                break
        if path:
            paths.append(tuple(path))
    minimal = []
    for path in sorted(set(paths), key=lambda p: (len(p), str(p))):
        if not any(path[:len(existing)] == existing for existing in minimal):
            minimal.append(path)
    return [message for _location, message in errors], tuple(minimal)


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
        if set(item) != {'path', 'value'}:
            raise ValueError('补丁仅接受 path 与 value')
        path = tuple(item['path'])
        if path not in paths or path in seen:
            raise ValueError('补丁越界或重复')
        seen.add(path)
        target = result
        for part in path[:-1]:
            target = target[part]
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


def replay_semantic_trace(trace: tuple[dict, ...] | list[dict]) -> dict:
    """从已核验原响应逐步重建候选；不是从最后patch猜完整业务。"""
    candidate = None
    for step in trace:
        if set(step) != {'kind', 'value', 'paths', 'ref'}:
            raise ValueError('语义恢复trace字段不完整')
        if step['kind'] == 'result' and candidate is None and not step['paths']:
            candidate = step['value']
        elif step['kind'] == 'patch' and candidate is not None:
            candidate = _patch(candidate, step['value'], tuple(tuple(path) for path in step['paths']))
        elif step['kind'] == 'repair' and candidate is not None:
            candidate = _revision_patch(candidate, step['value'],
                tuple(tuple(path) for path in step['paths']['replace']),
                tuple(tuple(path) for path in step['paths']['append']),
                tuple(tuple(path) for path in step['paths']['remove']))
        elif step['kind'] == 'changes' and candidate is not None:
            if not isinstance(step['value'], list) or step['paths']:
                raise ValueError('组合changes须携带原始完整语义trace')
            if not step['value'] or step['ref'] != step['value'][-1]['ref']:
                raise ValueError('组合changes末响应身份不匹配')
            candidate = _changes(candidate, replay_semantic_trace(step['value']))
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


def request_json(session: PaidRequestSession, owner: str, request: ChatRequest,
                 compile_value: Callable[[dict], Any], *, repository=None,
                 index: SourceIndex | None = None, max_chapter: int | None = None,
                 revision_seed: RevisionSeed | None = None) -> SemanticResult:
    """每个回答先耐久；材料续答与窄修均使用原 owner 的持久额度。"""
    refs = list(revision_seed.refs) if revision_seed is not None else []
    seen = set()
    candidate = revision_seed.baseline if revision_seed is not None else None
    patch_paths: tuple[tuple, ...] = revision_seed.paths if revision_seed is not None else ()
    append_paths = revision_seed.append_paths if revision_seed is not None else ()
    remove_paths = revision_seed.remove_paths if revision_seed is not None else ()
    material_cache: list[dict] = []
    trace: list[dict] = list(revision_seed.trace) if revision_seed is not None else []
    previous_defects: frozenset[tuple] | None = None
    base_user = request.user
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
                materials = [_material(index, query, max_chapter) for query in queries]
                identity = digest(materials)
                if identity in seen:
                    raise ValueError('重复材料请求无进展')
                seen.add(identity)
                if repository is not None:
                    repository.write(f'runtime/materials/{identity}.json', {
                        'contract_version': 'story-v1', 'owner': owner,
                        'request_sha256': answer.request_sha256,
                        'source_identity': index.source_identity, 'materials': materials})
                session.record_acceptance(answer, 'json', parse='ok', accepted=True,
                                          response_kind='material',
                                          salvage='reasoning' if not (answer.content or '').strip() else 'none')
                refs.append(answer.ref)
                material_cache.extend(materials)
                request = replace(request, user=base_user + '\n【已冻结补充材料】\n'
                                  + json.dumps(material_cache, ensure_ascii=False))
                continue
            merged = (_revision_patch(candidate, value, patch_paths, append_paths, remove_paths)
                if revision_seed is not None else _patch(candidate, value, patch_paths) if patch_paths else value)
            if revision_seed is not None and merged == revision_seed.baseline:
                raise ValidationStop('回派补丁没有改变任何授权条目', code='owner_revision_no_progress',
                    node=semantic_owner_unit(owner)[0], stage=semantic_owner_unit(owner)[0].split('.')[1],
                    item=semantic_owner_unit(owner)[1], phase='accept')
            trace.append({'kind': 'repair' if revision_seed is not None else 'patch' if patch_paths else 'result', 'value': value,
                          'paths': {'replace': [list(path) for path in patch_paths],
                                    'append': [list(path) for path in append_paths],
                                    'remove': [list(path) for path in remove_paths]}
                              if revision_seed is not None else [list(path) for path in patch_paths],
                          'ref': dict(answer.ref._asdict())})
            compile_value(merged)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            current = merged if isinstance(merged, dict) else (candidate if revision_seed is not None or patch_paths else value)
            errors, paths = _diagnose(exc, current if isinstance(current, dict) else {})
            empty = not (answer.content or '').strip()
            session.record_acceptance(answer, 'json', parse='empty' if empty else 'failed',
                errors=errors, salvage='reasoning' if empty and answer.reasoning else 'none', accepted=False,
                response_kind='patch' if revision_seed is not None else _response_kind(owner, value, patch_paths))
            defects = _defect_identity(exc, paths)
            signature = digest(sorted(defects, key=str))
            node, item = semantic_owner_unit(owner)
            if getattr(exc, 'node', node) != node or hasattr(exc, 'problems'):
                exc.semantic_result = SemanticResult(current, tuple((*refs, answer.ref)), answer, tuple(trace))
                if owner == 'book' and getattr(exc, 'source_issues', None) and repository is not None:
                    repository.write('runtime/source_repairs/book_candidate.json', {
                        'contract_version': 'story-v1', 'request_sha256': answer.request_sha256,
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
                    context={'errors': errors, 'paths': paths}) from exc
            previous_defects = defects
            if not paths and not isinstance(current, dict):
                seen.add(signature)
                request = replace(request, user=base_user + '\n【格式修复】上轮未能解码为唯一键JSON对象：'
                    + '；'.join(errors) + '\n仅重新提交符合原schema的JSON；不得改变任务scope。')
                continue
            if not paths:
                raise ValidationStop('语义候选未通过：' + '；'.join(errors), code='story_candidate',
                                     node=node, stage=node.split('.')[1], item=item, phase='accept',
                                     context={'errors': errors, 'paths': paths}) from exc
            if revision_seed is not None:
                bounded = tuple(path for path in revision_seed.paths
                    if any(path[:len(found)] == found or found[:len(path)] == path for found in paths))
                if not bounded:
                    raise ValidationStop('修订需要授权外位置，须显式扩大范围', code='revision_scope_required',
                        node=node, stage=node.split('.')[1], item=item, phase='accept',
                        context={'errors': errors, 'paths': paths}) from exc
                paths = bounded
            seen.add(signature)
            candidate = current
            patch_paths = paths
            if merged is not None:
                # 已合并的结构操作在trace中保留；后续字段窄修不得重复追加或删除。
                append_paths, remove_paths = (), ()
            refs.append(answer.ref)
            request = replace(request, user=base_user + '\n【冻结补充材料】\n'
                + json.dumps(material_cache, ensure_ascii=False) + '\n【冻结候选与窄修权限】\n'
                + json.dumps({'candidate': candidate, 'errors': errors,
                              'allowed_paths': paths}, ensure_ascii=False)
                + '\n仅返回 {"patches":[{"path":[给定路径],"value":完整替换条目}]}'
                + ('，另必填appends（只可使用原授权容器）。' if append_paths else '。')
                + ('另必填removes（只可删除原授权字典语义键）。' if remove_paths else '')
                + '不得改其他条目，不得删除未授权条目或重排既有数组。')
            continue
        session.record_acceptance(answer, 'json', parse='ok', accepted=True,
            salvage='reasoning' if not (answer.content or '').strip() else 'none',
            response_kind='patch' if revision_seed is not None else _response_kind(owner, value, patch_paths))
        refs.append(answer.ref)
        return SemanticResult(merged, tuple(refs), answer, tuple(trace))
