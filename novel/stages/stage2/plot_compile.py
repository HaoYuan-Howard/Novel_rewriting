"""段号响应编译为完整剧情蓝图；不补写剧情，不推导语义通过。

同一段范围在一章中只生成一个 EvidenceAnchor。模型不计算字符坐标，
共享段落和重叠证据合法，不能用区间覆盖替代剧情保真审阅。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any

from contracts.schema.plot import PlotBlueprint, PlotChapterResponse, unique
from novel.stages.stage2.paragraphs import span_of


def source_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def compile_chapter(
    response: PlotChapterResponse, source: str, *,
    allowed_char_ids: frozenset[str],
    allowed_entry_state_refs: frozenset[str] = frozenset(),
    allowed_global_refs: frozenset[tuple[str, str]] = frozenset(),
    next_ids: Mapping[str, int] | None = None,
) -> PlotBlueprint:
    """分配正式标识；编辑/重抽可传入从历史计算的单调 ID 游标，禁止复用退役 ID。"""
    ordinal = response.chapter_ordinal
    payload = response.model_dump(mode='json')
    mapping: dict[str, str] = {}
    starts = dict(next_ids or {})
    for collection, key, prefix in (
            ('beats', 'beat_key', 'b'), ('states', 'state_key', 'st'),
            ('constraints', 'constraint_key', 'c')):
        keys = [row[key] for row in payload[collection]]
        unique(keys, collection)
        start = starts.get(prefix, 1)
        if type(start) is not int or start < 1:
            raise ValueError('正式 ID 游标必须为正整数')
        for number, local in enumerate(keys, start):
            mapping[local] = f'{prefix}{ordinal:02d}_{number:02d}'

    anchors: dict[tuple[int, int], dict[str, Any]] = {}
    character_scalars = {
        'holder_char_id', 'observer_char_id', 'narrator_char_id', 'focalizer_char_id'}
    character_lists = {'participant_char_ids', 'present_char_ids'}
    id_fields = {'beat_key': 'beat_id', 'state_key': 'state_id',
                 'constraint_key': 'constraint_id'}

    def ref(value: str) -> str:
        if value in mapping:
            return mapping[value]
        if value in allowed_entry_state_refs:
            return value
        raise ValueError(f'局部或章首引用不存在：{value}')

    def transform(value: Any) -> Any:
        if isinstance(value, list):
            return [transform(item) for item in value]
        if not isinstance(value, dict):
            return value
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
                         'target_beat_ref', 'state_ref', 'at_beat_ref'):
                result[key] = None if item is None else ref(item)
            elif key in ('entry_state_refs', 'carry_out_refs', 'supersedes_state_refs'):
                result[key] = [ref(v) for v in item]
            elif key == 'global_ref' and item is not None:
                if ((item['kind'], item['ref']) not in allowed_global_refs
                        and not (item['kind'] == 'character' and item['ref'] in allowed_char_ids)):
                    raise ValueError('状态引用了请求未提供的 Global 主体')
                result[key] = item
            elif key in character_scalars:
                if item is not None and item not in allowed_char_ids:
                    raise ValueError(f'{key} 引用了不可见人物：{item}')
                result[key] = item
            elif key in character_lists:
                if item is not None and not set(item) <= allowed_char_ids:
                    raise ValueError(f'{key} 引用了不可见人物')
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
    return chapter


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


def validate_book(chapters: Sequence[PlotBlueprint],
                  sources: Mapping[int, str]) -> None:
    ordinals = [row.chapter_ordinal for row in chapters]
    if ordinals != sorted(sources) or sorted(sources) != list(range(1, len(sources) + 1)):
        raise ValueError('正式蓝图必须按阅读序完整覆盖当前源章')
    carried: frozenset[str] = frozenset()
    for chapter in chapters:
        validate_evidence(chapter, sources[chapter.chapter_ordinal])
        validate_entry_states(chapter, carried)
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
    carried: frozenset[str] = frozenset()
    for chapter in sorted(response.blueprints, key=lambda row: row.chapter_ordinal):
        if chapter.chapter_ordinal not in sources:
            raise ValueError('抽取响应越出当前原文章域')
        result = compile_chapter(
            chapter, sources[chapter.chapter_ordinal],
            allowed_char_ids=frozenset(visible.get(chapter.chapter_ordinal, {})),
            allowed_entry_state_refs=carried,
            allowed_global_refs=frozenset(
                (row['kind'], row['ref']) for row in
                (global_projections or {}).get(str(chapter.chapter_ordinal), [])))
        compiled.append(result.model_dump(mode='json'))
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
                                (chapter.constraints, 'constraint_id', 'c')):
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
                          'constraint_id': 'constraint_key'}.get(key, key)
                if key in ('beat_id', 'state_id', 'constraint_id', 'beat_ref',
                           'before_beat_ref', 'after_beat_ref', 'target_beat_ref',
                           'state_ref', 'at_beat_ref'):
                    result[target] = mapping.get(item, item)
                elif key in ('entry_state_refs', 'carry_out_refs', 'supersedes_state_refs'):
                    result[target] = [mapping.get(ref, ref) for ref in item]
                else:
                    result[target] = project(item)
        return result
    projected = project(chapter.model_dump(mode='json'))
    return PlotChapterResponse.model_validate(projected).model_dump(mode='json')
