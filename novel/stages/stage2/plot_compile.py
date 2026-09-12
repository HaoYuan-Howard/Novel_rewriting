"""段号响应编译为完整剧情蓝图；不补写剧情，不推导语义通过。

同一段范围在一章中只生成一个 EvidenceAnchor。模型不计算字符坐标，
共享段落和重叠证据合法，不能用区间覆盖替代剧情保真审阅。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any

from contracts.schema.plot import ActorBinding, ActorRef, PlotBlueprint, PlotChapterResponse, unique
from novel.stages.stage2.character_refs import null_binding_conflict
from novel.stages.stage2.paragraphs import span_of


def source_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def compile_chapter(
    response: PlotChapterResponse, source: str, *,
    allowed_char_ids: frozenset[str],
    allowed_entry_state_refs: frozenset[str] = frozenset(),
    allowed_global_refs: frozenset[tuple[str, str]] = frozenset(),
    character_bindings: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    allowed_actor_bindings: Mapping[str, Any] | None = None,
    global_subjects: Mapping[tuple[str, str], Any] | None = None,
    next_ids: Mapping[str, int] | None = None,
) -> PlotBlueprint:
    """分配正式标识；编辑/重抽可传入从历史计算的单调 ID 游标，禁止复用退役 ID。"""
    ordinal = response.chapter_ordinal
    payload = response.model_dump(mode='json')
    mapping: dict[str, str] = {}
    starts = dict(next_ids or {})
    for collection, key, prefix in (
            ('beats', 'beat_key', 'b'), ('states', 'state_key', 'st'),
            ('constraints', 'constraint_key', 'c'), ('actor_bindings', 'presentation_key', 'ap')):
        keys = [row[key] for row in payload[collection]]
        unique(keys, collection)
        start = starts.get(prefix, 1)
        if type(start) is not int or start < 1:
            raise ValueError('正式 ID 游标必须为正整数')
        for number, local in enumerate(keys, start):
            mapping[local] = f'{prefix}{ordinal:02d}_{number:02d}'

    anchors: dict[tuple[int, int], dict[str, Any]] = {}
    id_fields = {'beat_key': 'beat_id', 'state_key': 'state_id',
                 'constraint_key': 'constraint_id', 'presentation_key': 'presentation_id'}
    inherited_bindings = dict(allowed_actor_bindings or {})

    def ref(value: str) -> str:
        if value in mapping:
            return mapping[value]
        if value in allowed_entry_state_refs or value in inherited_bindings:
            return value
        raise ValueError(f'局部或章首引用不存在：{value}')

    def transform(value: Any) -> Any:
        if isinstance(value, list):
            return [transform(item) for item in value]
        if not isinstance(value, dict):
            return value
        if value.get('kind') == 'relation' and 'relation_actors' in value:
            if ('relation', value['ref']) not in allowed_global_refs:
                raise ValueError('关系呈现引用请求未提供的 Global 关系')
            supplied = (global_subjects or {}).get(('relation', value['ref']))
            if supplied is None:
                raise ValueError('关系呈现缺少本轮提供的正式关系端点')
            source_relation = supplied.get('value', supplied)
            ends = value['relation_actors']
            if (ends['subject']['char_id'], ends['object']['char_id']) != (
                    source_relation['subject_id'], source_relation['object_id']):
                raise ValueError('关系呈现改变了正式关系的有向端点')
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == 'evidence':
                refs: list[str] = []
                for span in item:
                    pair = (span['start_para'], span['end_para'])
                    if pair not in anchors:
                        start, end = span_of(source, *pair)
                        anchors[pair] = {
                            'evidence_id': f'e{ordinal:02d}_{len(anchors) + 1:02d}',
                            'start_para': pair[0], 'end_para': pair[1],
                            'start': start, 'end': end,
                        }
                    refs.append(anchors[pair]['evidence_id'])
                unique(refs, '响应证据')
                result['evidence_refs'] = refs
            elif key in id_fields:
                result[id_fields[key]] = ref(item)
            elif key in ('beat_ref', 'before_beat_ref', 'after_beat_ref',
                         'target_beat_ref', 'state_ref', 'at_beat_ref', 'presentation_ref', 'public_state_ref'):
                result[key] = None if item is None else ref(item)
            elif key in ('entry_state_refs', 'carry_out_refs', 'supersedes_state_refs', 'beat_refs'):
                result[key] = [ref(v) for v in item]
            elif key == 'global_ref' and item is not None:
                if ((item['kind'], item['ref']) not in allowed_global_refs
                        and not (item['kind'] == 'character' and item['ref'] in allowed_char_ids)):
                    raise ValueError('状态引用了请求未提供的 Global 主体')
                result[key] = transform(item)
            elif key == 'char_id':
                if item not in allowed_char_ids:
                    raise ValueError(f'ActorRef 引用了请求未提供的人物：{item}')
                result[key] = item
            else:
                result[key] = transform(item)
        return result

    compiled = transform(payload)
    compiled['source_sha256'] = source_digest(source)
    compiled['evidence_anchors'] = list(anchors.values())
    chapter = PlotBlueprint.model_validate(compiled)
    validate_evidence(chapter, source)
    validate_entry_states(chapter, allowed_entry_state_refs)
    validate_actors(chapter, source, allowed_char_ids=allowed_char_ids,
                    character_bindings=character_bindings, inherited=inherited_bindings)
    return chapter


def actor_refs(value: Any) -> list[ActorRef]:
    """显式遍历 ActorRef 字段；绑定定义和普通正文不是人物使用位置。"""
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, list):
        return [ref for item in value for ref in actor_refs(item)]
    if not isinstance(value, dict):
        return []
    if set(value) == {'char_id', 'presentation_ref'}:
        return [ActorRef.model_validate(value)]
    return [ref for item in value.values() for ref in actor_refs(item)]


def binding_catalog(chapters: Sequence[PlotBlueprint]) -> dict[str, ActorBinding]:
    result = {}
    for chapter in chapters:
        for row in chapter.actor_bindings:
            if row.presentation_id in result:
                raise ValueError('跨章表面身份 ID 重复')
            result[row.presentation_id] = row
    return result


def validate_actors(chapter: PlotBlueprint, source: str, *,
                    allowed_char_ids: frozenset[str] | None = None,
                    character_bindings: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
                    inherited: Mapping[str, Any] | None = None,
                    check_registered: bool = True) -> None:
    bindings = {key: value if isinstance(value, ActorBinding) else ActorBinding.model_validate(value)
                for key, value in (inherited or {}).items()}
    local = {row.presentation_id: row for row in chapter.actor_bindings}
    if bindings.keys() & local.keys():
        raise ValueError('本章表面身份不能覆盖携入定义')
    bindings.update(local)
    anchors = {row.evidence_id: row for row in chapter.evidence_anchors}
    for binding in local.values():
        if allowed_char_ids is not None and binding.char_id not in allowed_char_ids:
            raise ValueError('表面身份引用请求未提供的人物')
        excerpts = [source[anchors[ref].start:anchors[ref].end] for ref in binding.evidence_refs]
        if check_registered and character_bindings is None:
            raise ValueError('表面身份缺少本轮登记称谓候选，不能猜测归属')
        registered = {row['surface'] for row in (character_bindings or {}).get(binding.char_id, [])}
        if check_registered and not set(binding.surfaces) <= registered:
            raise ValueError('表面身份引用未登记或归属错误的称谓')
        if any(not any(surface in text for text in excerpts) for surface in binding.surfaces):
            raise ValueError('表面身份的称谓没有本章证据字面支持')
    uses = actor_refs(chapter)
    used_bindings = {row.presentation_ref for row in uses if row.presentation_ref}
    for actor in uses:
        if allowed_char_ids is not None and actor.char_id not in allowed_char_ids:
            raise ValueError('人物使用位置引用未提供的实体')
        if actor.presentation_ref is not None:
            binding = bindings.get(actor.presentation_ref)
            if binding is None or binding.char_id != actor.char_id:
                raise ValueError('人物呈现绑定缺失、未提供或与实体不符')
        else:
            choices = [row for row in bindings.values() if row.char_id == actor.char_id]
            if len(choices) > 1:
                raise ValueError('同一实体存在多个表面身份，必须明确 presentation_ref')
            names = list((character_bindings or {}).get(actor.char_id, []))
            if len(choices) == 1:
                if null_binding_conflict(names, choices[0].surfaces,
                                         chapter_ordinal=chapter.chapter_ordinal):
                    raise ValueError('默认人物引用仍有此前公开称谓，不能吸入尚未揭示的新表面身份')
                used_bindings.add(choices[0].presentation_id)
            if not choices and len(names) > 1 and any(
                    row['identity_revealed_from'] >= chapter.chapter_ordinal for row in names):
                raise ValueError('本章或未来才揭示的多称谓映射必须显式绑定，不能在章首合并')
    if not local.keys() <= used_bindings:
        raise ValueError('稀疏表面身份表只能保存本章实际使用的绑定')


class _ActorBindingScopes:
    """按真实章节输入解析呈现；原章默认引用不受后来无关绑定影响。"""

    def __init__(self, chapters: Sequence[PlotBlueprint]):
        self.chapters = {chapter.chapter_ordinal: chapter for chapter in chapters}
        self.ordinals = sorted(self.chapters)
        self.states = {row.state_id: (chapter, row) for chapter in chapters for row in chapter.states}
        self.bindings = {row.presentation_id: (chapter, row) for chapter in chapters for row in chapter.actor_bindings}
        self.scopes: dict[int, dict[str, ActorBinding]] = {}

    def available(self, ordinal: int) -> dict[str, ActorBinding]:
        if ordinal not in self.chapters:
            raise ValueError('人物呈现缺少原始使用章，不能按当前章猜测')
        if ordinal not in self.scopes:
            result = {}
            index = self.ordinals.index(ordinal)
            if index:
                previous = self.chapters[self.ordinals[index - 1]]
                for ref in previous.carry_out_refs:
                    origin = self.states.get(ref)
                    if origin is None or origin[0].chapter_ordinal > previous.chapter_ordinal:
                        raise ValueError('携出 State 缺少当时已有的定义')
                    result.update(self.state_bindings(ref))
            for binding in self.chapters[ordinal].actor_bindings:
                if binding.presentation_id in result:
                    raise ValueError('本章表面身份不能覆盖携入定义')
                result[binding.presentation_id] = binding
            self.scopes[ordinal] = result
        return dict(self.scopes[ordinal])

    def _state_uses(self, ref: str) -> set[str]:
        if ref not in self.states:
            raise ValueError(f'携入 State 缺少原始定义：{ref}')
        origin, state = self.states[ref]
        available = self.available(origin.chapter_ordinal)
        result = set()
        for actor in actor_refs(state):
            if actor.presentation_ref is not None:
                binding = available.get(actor.presentation_ref)
                if binding is None or binding.char_id != actor.char_id:
                    raise ValueError('携入 State 的呈现不在其原章提供的绑定范围内')
                result.add(actor.presentation_ref)
            else:
                matches = [binding for binding in available.values() if binding.char_id == actor.char_id]
                if len(matches) > 1:
                    raise ValueError('携入 State 的原章默认人物呈现有歧义，须免费明确绑定')
                if matches:
                    result.add(matches[0].presentation_id)
        return result

    def state_bindings(self, ref: str) -> dict[str, ActorBinding]:
        pending, result = sorted(self._state_uses(ref)), {}
        while pending:
            key = pending.pop(0)
            if key in result:
                continue
            if key not in self.bindings:
                raise ValueError('携入 State 的表面身份缺少原始定义')
            origin, binding = self.bindings[key]
            result[key] = binding
            if binding.public_state_ref is not None:
                public_origin = self.states.get(binding.public_state_ref)
                if public_origin is None or public_origin[0].chapter_ordinal > origin.chapter_ordinal:
                    raise ValueError('表面身份引用的公开 State 缺少当时已有的定义')
                pending.extend(sorted(self._state_uses(binding.public_state_ref) - result.keys()))
        return result


def available_actor_bindings(chapters: Sequence[PlotBlueprint], ordinal: int) -> dict[str, ActorBinding]:
    """本章声明 + 上一章 carry_out 实际提供的 State 闭包；不是全书历史目录。"""
    return _ActorBindingScopes(chapters).available(ordinal)


def state_materials(chapters: Sequence[PlotBlueprint], state_refs: Sequence[str]) -> list[dict[str, Any]]:
    """携实际 State 与原章解析的呈现/证据闭包；State 中原有 null 不被改写。"""
    scope = _ActorBindingScopes(chapters)
    states, bindings = scope.states, scope.bindings
    result = []
    for ref in sorted(state_refs):
        if ref not in states:
            raise ValueError(f'携入 State 缺少原始定义：{ref}')
        chapter, state = states[ref]
        anchors = {row.evidence_id: row for row in chapter.evidence_anchors}
        needed = scope.state_bindings(ref)
        actors = []
        pending, visited = sorted(needed), set()
        while pending:
            key = pending.pop(0)
            if key in visited:
                continue
            visited.add(key)
            if key not in bindings:
                raise ValueError('携入 State 的表面身份缺少原始定义')
            origin, binding = bindings[key]
            evidence = {row.evidence_id: row for row in origin.evidence_anchors}
            public_state = None
            if binding.public_state_ref is not None:
                if binding.public_state_ref not in states:
                    raise ValueError('表面身份引用的公开 State 缺少原始定义')
                public_origin, public = states[binding.public_state_ref]
                pending.extend(sorted({actor.presentation_ref for actor in actor_refs(public)
                    if actor.presentation_ref and actor.presentation_ref not in visited}))
                public_anchors = {row.evidence_id: row for row in public_origin.evidence_anchors}
                public_state = {'chapter_ordinal': public_origin.chapter_ordinal,
                    'state': public.model_dump(mode='json'), 'source_sha256': public_origin.source_sha256,
                    'evidence_anchors': [public_anchors[item].model_dump(mode='json') for item in public.evidence_refs]}
            actors.append({'chapter_ordinal': origin.chapter_ordinal, 'binding': binding.model_dump(mode='json'),
                'public_state': public_state,
                'source_sha256': origin.source_sha256,
                'evidence_anchors': [evidence[item].model_dump(mode='json') for item in binding.evidence_refs]})
        result.append({'chapter_ordinal': chapter.chapter_ordinal, 'state': state.model_dump(mode='json'),
            'source_sha256': chapter.source_sha256,
            'evidence_anchors': [anchors[item].model_dump(mode='json') for item in state.evidence_refs],
            'actor_bindings': sorted(actors, key=lambda row: row['binding']['presentation_id'])})
    return result


def validate_entry_states(chapter: PlotBlueprint,
                          available: frozenset[str]) -> None:
    local = {state.state_id: state for state in chapter.states}
    for ref in chapter.entry_state_refs:
        if ref in local:
            if local[ref].established_at.kind != 'at_entry':
                raise ValueError('章首不能引用尚未成立的本章状态')
        elif ref not in available:
            raise ValueError(f'章首状态未由已提供的上游支持：{ref}')


def validate_evidence(chapter: PlotBlueprint, source: str) -> None:
    """同一函数服务生成和免费重验；范围合法不授予语义接受。"""
    if chapter.source_sha256 != source_digest(source):
        raise ValueError('蓝图绑定的原文已经变化')
    anchors = {row.evidence_id: row for row in chapter.evidence_anchors}
    for anchor in anchors.values():
        if span_of(source, anchor.start_para, anchor.end_para) != (anchor.start, anchor.end):
            raise ValueError(f'{anchor.evidence_id} 坐标与当前段落索引不一致')
    for constraint in chapter.constraints:
        if constraint.kind == 'literal':
            excerpts = [source[anchors[ref].start:anchors[ref].end]
                        for ref in constraint.evidence_refs]
            if not any(constraint.value in excerpt for excerpt in excerpts):
                raise ValueError(f'{constraint.constraint_id} 必要字面没有原文依据')


def evidence_texts(chapter: PlotBlueprint, source: str,
                   refs: Sequence[str]) -> list[str]:
    validate_evidence(chapter, source)
    anchors = {row.evidence_id: row for row in chapter.evidence_anchors}
    if not set(refs) <= anchors.keys():
        raise ValueError('证据引用越界')
    return [source[anchors[ref].start:anchors[ref].end] for ref in refs]


def recompile_evidence(chapter: PlotBlueprint, source: str) -> PlotBlueprint:
    """只重算程序拥有的坐标，不能把旧语义悄悄绑定到另一份原文。"""
    if chapter.source_sha256 != source_digest(source):
        raise ValueError('源文身份已变化；需要明确修订并重开受影响审阅，不能免费重签')
    raw = chapter.model_dump(mode='json')
    for row in raw['evidence_anchors']:
        row['start'], row['end'] = span_of(source, row['start_para'], row['end_para'])
    rebuilt = PlotBlueprint.model_validate(raw)
    validate_evidence(rebuilt, source)
    return rebuilt


def validate_book_sources(chapters: Sequence[PlotBlueprint], sources: Mapping[int, str]) -> None:
    """编辑可保留待订正的语义引用，但不能重签不同原文或放宽章集合。"""
    ordinals = [row.chapter_ordinal for row in chapters]
    if ordinals != sorted(sources) or sorted(sources) != list(range(1, len(sources) + 1)):
        raise ValueError('正式蓝图必须按阅读序完整覆盖当前源章')
    for chapter in chapters:
        validate_evidence(chapter, sources[chapter.chapter_ordinal])


def validate_book(chapters: Sequence[PlotBlueprint], sources: Mapping[int, str]) -> None:
    validate_book_sources(chapters, sources)
    carried: frozenset[str] = frozenset()
    earlier = []
    for chapter in chapters:
        validate_evidence(chapter, sources[chapter.chapter_ordinal])
        validate_entry_states(chapter, carried)
        inherited = {row['binding']['presentation_id']: row['binding']
            for material in state_materials(earlier, sorted(carried)) for row in material['actor_bindings']}
        validate_actors(chapter, sources[chapter.chapter_ordinal], inherited=inherited, check_registered=False)
        earlier.append(chapter)
        carried = frozenset(chapter.carry_out_refs)


def compile_plot_response(raw: dict[str, Any], main_text: list[dict[str, Any]], *,
                          character_inventory: Mapping[str, Any],
                          global_projections: Mapping[str, list[dict[str, Any]]] | None = None,
                          **_unused: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """正式章节 producer 的编译接口；不构造任何旧事实或 hinge 适配器。"""
    from contracts.schema.plot import PlotBlueprintResponse
    response = PlotBlueprintResponse.model_validate(raw)
    sources = {row['chapter_ordinal']: row['text'] for row in main_text}
    visible = character_inventory['visible_character_refs']
    compiled = []
    compiled_typed: list[PlotBlueprint] = []
    carried: frozenset[str] = frozenset()
    for chapter in sorted(response.blueprints, key=lambda row: row.chapter_ordinal):
        if chapter.chapter_ordinal not in sources:
            raise ValueError('抽取响应越出当前原文章域')
        result = compile_chapter(
            chapter, sources[chapter.chapter_ordinal],
            allowed_char_ids=frozenset(visible.get(chapter.chapter_ordinal, {})),
            allowed_entry_state_refs=carried,
            character_bindings=character_inventory.get('character_bindings', {}).get(chapter.chapter_ordinal),
            allowed_actor_bindings={row['binding']['presentation_id']: row['binding']
                for material in state_materials(compiled_typed, sorted(carried))
                for row in material['actor_bindings']} if compiled else {},
            allowed_global_refs=frozenset(
                (row['kind'], row['ref']) for row in
                (global_projections or {}).get(str(chapter.chapter_ordinal), [])),
            global_subjects={(row['kind'], row['ref']): row for row in
                (global_projections or {}).get(str(chapter.chapter_ordinal), [])})
        compiled.append(result.model_dump(mode='json'))
        compiled_typed.append(result)
        carried = frozenset(result.carry_out_refs)
    return {'blueprints': compiled}, {}


def response_projection(raw: dict[str, Any], source: str) -> dict[str, Any]:
    """人工修订/定向响应的反投影；证据回到段号，不转抄原文。"""
    chapter = PlotBlueprint.model_validate(raw)
    validate_evidence(chapter, source)
    anchors = {row.evidence_id: row for row in chapter.evidence_anchors}
    mapping: dict[str, str] = {}
    for rows, field, prefix in ((chapter.beats, 'beat_id', 'b'),
                                (chapter.states, 'state_id', 'st'),
                                (chapter.constraints, 'constraint_id', 'c'),
                                (chapter.actor_bindings, 'presentation_id', 'a')):
        for index, row in enumerate(rows, 1):
            mapping[getattr(row, field)] = f'{prefix}{index}'

    def project(value: Any) -> Any:
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key in ('source_sha256', 'evidence_anchors'):
                continue
            if key == 'evidence_refs':
                result['evidence'] = [{'start_para': anchors[ref].start_para,
                                       'end_para': anchors[ref].end_para} for ref in item]
            else:
                target = {'beat_id': 'beat_key', 'state_id': 'state_key',
                          'constraint_id': 'constraint_key', 'presentation_id': 'presentation_key'}.get(key, key)
                if key in ('beat_id', 'state_id', 'constraint_id', 'beat_ref',
                           'before_beat_ref', 'after_beat_ref', 'target_beat_ref',
                           'state_ref', 'at_beat_ref', 'presentation_id', 'presentation_ref', 'public_state_ref'):
                    result[target] = mapping.get(item, item)
                elif key in ('entry_state_refs', 'carry_out_refs', 'supersedes_state_refs', 'beat_refs'):
                    result[target] = [mapping.get(ref, ref) for ref in item]
                else:
                    result[target] = project(item)
        return result
    projected = project(chapter.model_dump(mode='json'))
    return PlotChapterResponse.model_validate(projected).model_dump(mode='json')
