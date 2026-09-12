"""材料与依赖身份共享的章级证据闭包，B 和结构审阅不各自重新拼全书。"""
from __future__ import annotations

import copy
import hashlib
from typing import Any
from contracts.schema.plot import PlotBlueprint
from contracts.schema.knowledge_links import KnowledgeLinks
from contracts.schema.plot_materials import PlotChapterReviewMaterials
from novel.errors import RefusedByStructure
from novel.stages.stage4.plot_binding import source_context_catalog


def _compile_materials(ordinal: int, blueprints: dict, grounding: dict,
                       knowledge_links: dict, main_text: list[dict],
                       subject_refs: list[dict], *, materialize: bool) -> dict:
    bps = {r['chapter_ordinal']: PlotBlueprint.model_validate(r) for r in blueprints['blueprints']}
    bp = bps[ordinal]
    texts = {r['chapter_ordinal']: r['text'] for r in main_text}
    links = KnowledgeLinks.model_validate(knowledge_links)
    decisions = {r.obligation_id: r for r in links.links}
    needed = {(r['kind'], r['ref']) for r in subject_refs}
    local_subjects = needed | {('beat', r.beat_id) for r in bp.beats}
    local_subjects.update(('state', r.state_id) for r in bp.states)
    local_subjects.update(('state', ref) for ref in bp.entry_state_refs)
    events = [r for r in links.scope.obligations if r.chapter_ordinal == ordinal or (
        r.kind in ('reader_reveal', 'character_knowledge')
        and (r.subject_ref.kind, r.subject_ref.ref) in local_subjects)]
    needed.update((r.subject_ref.kind, r.subject_ref.ref) for r in events)
    support = {('state', ref) for ref in bp.entry_state_refs}
    for event in events:
        if event.chapter_ordinal == ordinal:
            support.update((r.kind, r.ref) for r in decisions[event.obligation_id].resolution.subject_refs)
        if event.audience and event.audience.kind == 'character':
            needed.add(('character', event.audience.char_id))
    needed |= support
    known = {}
    for chapter in bps.values():
        for kind, rows, field in (('beat', chapter.beats, 'beat_id'),
                                   ('state', chapter.states, 'state_id')):
            for row in rows:
                known[kind, getattr(row, field)] = (chapter, row.model_dump(mode='json'))
    for key in tuple(needed):
        if key in known:
            value = known[key][1]
            if value.get('global_ref'):
                needed.add((value['global_ref']['kind'], value['global_ref']['ref']))
            if value.get('holder_char_id'):
                needed.add(('character', value['holder_char_id']))
            needed.update(('character', cid) for cid in value.get('participant_char_ids') or [])
    for beat in bp.beats:
        needed.update(('character', cid) for cid in beat.participant_char_ids)
        for context in beat.source_contexts:
            needed.update(('character', cid) for cid in context.present_char_ids or [])
    for state in bp.states:
        if state.global_ref:
            needed.add((state.global_ref.kind, state.global_ref.ref))
        if state.holder_char_id:
            needed.add(('character', state.holder_char_id))
    entities, canon = grounding['entities'], grounding['canon']
    # 两种正式 Global 视图的同一字段归属；不把人物动态结局混入静态名册。
    states = entities.get('character_states')
    if states is None:
        states = {ref: {k: row[k] for k in ('fate', 'fate_evidence') if k in row}
                  for ref, row in entities['characters'].items() if row.get('fate') is not None}
    global_raw = {
        **{('character', ref): {k: v for k, v in row.items() if k not in ('fate', 'fate_evidence')}
           for ref, row in entities['characters'].items()},
        **{('character_state', ref): row for ref, row in states.items()},
        **{('relation', row['relation_id']): row for row in entities['relations']},
        **{('world_rule', row['rule_id']): row for row in canon['world_rules']},
        **{('secret', row['secret_id']): row for row in canon['secrets']},
    }
    # Setting 的正式 alias 在 schema 中集中定义；不猜 evidence 字段名。
    from contracts.schema.stage2 import Setting
    for name, evidence_name in (('era', 'era_evidence'), ('worldview', 'worldview_evidence')):
        ref = Setting.model_fields[name].alias or name
        field = Setting.model_fields[evidence_name].alias or evidence_name
        global_raw['setting', ref] = {'value': canon['setting'][ref],
                                      'evidence': canon['setting'][field]}
    # 真正的依赖闭包：新增一层关系/状态引用后继续遍历，不能只扫一次。
    for segment in bp.narration:
        needed.update(('character', ref) for ref in
                      (segment.narrator_char_id, segment.focalizer_char_id) if ref)
    needed.update(('character', c.observer_char_id) for c in bp.constraints
                  if getattr(c, 'observer_char_id', None))
    pending, visited = list(needed), set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        additions = set()
        if key in known:
            _chapter, value = known[key]
            if value.get('global_ref'):
                additions.add((value['global_ref']['kind'], value['global_ref']['ref']))
            if value.get('holder_char_id'):
                additions.add(('character', value['holder_char_id']))
            additions.update(('state', ref) for ref in value.get('supersedes_state_refs') or [])
        if key in global_raw:
            value = global_raw[key]
            if key[0] == 'relation':
                additions.update(('character', value[field]) for field in ('subject_id', 'object_id'))
                if value['visibility']['kind'] == 'via_secret':
                    additions.add(('secret', value['visibility']['secret_id']))
            elif key[0] == 'secret':
                additions.update(('character', row['subject']['char_id'])
                                 for row in value['knowledge_events'] if row['subject']['kind'] == 'character')
            elif key[0] == 'character_state':
                additions.add(('character', key[1]))
        needed.update(additions)
        pending.extend(additions - visited)
    support.update(key for key in needed if key in known)
    evidence = {}
    requested = {ordinal: [(0, len(texts[ordinal]))]}

    def anchor(chapter: int, start: int, end: int, ref: str | None = None) -> str:
        if chapter not in texts or not 0 <= start < end <= len(texts[chapter]):
            raise RefusedByStructure('审阅证据不在当前源文内')
        ref = ref or f'source:{chapter}:{start}:{end}'
        row = {'evidence_ref': ref, 'chapter_ordinal': chapter, 'start': start, 'end': end}
        if ref in evidence and evidence[ref] != row:
            raise RefusedByStructure('同一证据引用出现不同坐标')
        evidence[ref] = row
        requested.setdefault(chapter, []).append((start, end))
        return ref

    def project(value: Any) -> Any:
        if isinstance(value, list):
            return [project(row) for row in value]
        if not isinstance(value, dict):
            return value
        if 'chapter_ordinal' in value and 'source' in value:
            source = value['source']
            if source is None:
                raise RefusedByStructure('必要 Global 证据尚未定位，不能省略后继续审阅')
            chapter = value['chapter_ordinal']
            ref = anchor(chapter, source['start'], source['end'])
            if texts[chapter][source['start']:source['end']] != source['text']:
                raise RefusedByStructure('Global 证据与当前源文字节不一致')
            return {'evidence_ref': ref}
        return {key: project(row) for key, row in value.items()}

    # 本章锚点已在 blueprint.evidence_anchors；本章原文完整提供，不再复制一张同义表。
    registered = {r.event_id: r.event for r in links.scope.registered_events if not r.retired}
    for event in events:
        declaration = registered.get(event.knowledge_ref)
        if declaration is None:
            continue
        for proof in declaration.evidence_refs:
            chapter = bps[proof.chapter_ordinal]
            existing = next((r for r in chapter.evidence_anchors if r.evidence_id == proof.evidence_ref), None)
            if existing:
                anchor(proof.chapter_ordinal, existing.start, existing.end, existing.evidence_id)
            else:
                from novel.stages.stage2.paragraphs import span_of
                _, start_para, end_para = proof.evidence_ref.split(':')
                start, end = span_of(texts[proof.chapter_ordinal], int(start_para), int(end_para))
                anchor(proof.chapter_ordinal, start, end)
    subjects = []
    for key in sorted(support):
        if key not in known:
            continue
        chapter, value = known[key]
        if chapter.chapter_ordinal == ordinal:
            continue
        selected = set(value['evidence_refs'])
        for row in chapter.evidence_anchors:
            if row.evidence_id in selected:
                anchor(chapter.chapter_ordinal, row.start, row.end, row.evidence_id)
        subjects.append({'subject_ref': {'kind': key[0], 'ref': key[1]}, 'value': copy.deepcopy(value)})
    globals_ = []
    for key in sorted(needed):
        if key[0] in ('beat', 'state', 'constraint', 'chapter_boundary'):
            continue
        if key not in global_raw:
            raise RefusedByStructure(f'审阅缺少必要共享主体：{key}')
        globals_.append({'subject_ref': {'kind': key[0], 'ref': key[1]},
                         'value': project(global_raw[key])})
    sources = []
    for chapter, ranges in sorted(requested.items()):
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        source = texts[chapter]
        # 外章只消费选中的证据。整章 SHA 会把未下发文字混进请求与接受身份；
        # canonical 源文、蓝图当前性及提交前后 CAS 仍由调用者的 Store 边界核验。
        sources.append({'chapter_ordinal': chapter,
                        'ranges': [{'start': start, 'end': end,
                                    **({'text': source[start:end]} if materialize else
                                       {'text_sha256': hashlib.sha256(
                                           source[start:end].encode('utf-8')).hexdigest()})}
                                   for start, end in merged]})
    return {
        'contract': 'plot-chapter-evidence-v1',
        'blueprint': bp.model_dump(mode='json'), 'support_subjects': subjects,
        'global_subjects': globals_, 'source_context_catalog': source_context_catalog(bp),
        'knowledge_events': [{
            'event': r.model_dump(mode='json', exclude={
                'candidate_subject_refs', 'entry_subject_refs', 'subject_sha256'}),
            'resolution': decisions[r.obligation_id].resolution.model_dump(mode='json'),
        } for r in events],
        'evidence_anchors': list(evidence.values()), 'source_materials': sources,
    }


def chapter_materials(ordinal: int, blueprints: dict, grounding: dict,
                      knowledge_links: dict, main_text: list[dict],
                      subject_refs: list[dict]) -> PlotChapterReviewMaterials:
    return PlotChapterReviewMaterials.model_validate(_compile_materials(
        ordinal, blueprints, grounding, knowledge_links, main_text, subject_refs, materialize=True))


def chapter_materials_identity(ordinal: int, blueprints: dict, grounding: dict,
                               knowledge_links: dict, main_text: list[dict],
                               subject_refs: list[dict]) -> str:
    """轻量依赖摘要：同一选择算法，只散列实际证据字节，不物化审阅原文包。"""
    from novel.consumption import consumed_digest
    return consumed_digest(_compile_materials(
        ordinal, blueprints, grounding, knowledge_links, main_text, subject_refs, materialize=False))


def material_identity(material: PlotChapterReviewMaterials) -> str:
    """验证下发正文与轻量依赖摘要一致，不能两条公式各自漂移。"""
    from novel.consumption import consumed_digest
    payload = material.model_dump(mode='json')
    for source in payload['source_materials']:
        for row in source['ranges']:
            row['text_sha256'] = hashlib.sha256(row.pop('text').encode('utf-8')).hexdigest()
    return consumed_digest(payload)
