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
                       subject_refs: list[dict], *, characters: dict, materialize: bool) -> dict:
    from contracts.schema.plot import PlotSubjectRef
    from novel.stages.stage4.actors import ActorCatalog, actual_subject_uses
    bps = {r['chapter_ordinal']: PlotBlueprint.model_validate(r) for r in blueprints['blueprints']}
    bp = bps[ordinal]
    texts = {r['chapter_ordinal']: r['text'] for r in main_text}
    links = KnowledgeLinks.model_validate(knowledge_links)
    decisions = {r.obligation_id: r for r in links.links}
    catalogs = {number: ActorCatalog(characters, blueprints, chapter_ordinal=number,
                source_text='\n'.join(texts[n] for n in sorted(texts) if n <= number),
                seed='private-review-materials') for number in bps}
    needed, bindings = {}, {}
    use_chapters = {}

    def add(value, ref=None, *, at_chapter=None):
        scope = at_chapter or ordinal
        row = (PlotSubjectRef(kind=value, ref=ref) if isinstance(value, str)
               else PlotSubjectRef.model_validate(value.model_dump(mode='json'))
               if hasattr(value, 'model_dump') else PlotSubjectRef.model_validate(value))
        catalog = catalogs[scope]
        if row.actor:
            row = row.model_copy(update={'actor': catalog.resolve(row.actor)})
        if row.relation_actors:
            row = row.model_copy(update={'relation_actors': row.relation_actors.model_copy(
                update={key: catalog.resolve(getattr(row.relation_actors, key))
                        for key in ('subject', 'object')})})
        needed[row.key()] = row
        use_chapters[row.key()] = min(scope, use_chapters.get(row.key(), scope))
        return row

    def actor(value, at_chapter=None):
        scope = at_chapter or ordinal
        catalog = catalogs[scope]
        resolved = catalog.resolve(value)
        add({'kind': 'character', 'ref': resolved.char_id,
             'actor': resolved.model_dump(mode='json')}, at_chapter=scope)
        if resolved.presentation_ref:
            binding = catalog.bindings[resolved.presentation_ref]
            bindings[resolved.presentation_ref] = binding
            if binding['public_state_ref']:
                add('state', binding['public_state_ref'])

    for ref in subject_refs:
        add(ref)
    for beat in bp.beats:
        add('beat', beat.beat_id)
    for state in bp.states:
        add('state', state.state_id)
    for ref in bp.entry_state_refs:
        add('state', ref)
    local_subjects = {row.raw_subject_key() for row in needed.values()}
    events = [r for r in links.scope.obligations if r.chapter_ordinal == ordinal or (
        r.kind in ('reader_reveal', 'character_knowledge')
        and r.subject_ref.raw_subject_key() in local_subjects)]
    all_states = {row.state_id: row for chapter in bps.values() for row in chapter.states}
    state_chapters = {row.state_id: chapter.chapter_ordinal
                      for chapter in bps.values() for row in chapter.states}
    available = [add(ref) for ref in [*subject_refs, *bp.relation_uses]]
    available.extend(add(row.global_ref, at_chapter=state_chapters[row.state_id])
        for row in [*bp.states, *(all_states[ref] for ref in bp.entry_state_refs)] if row.global_ref)
    for event in events:
        for ref in actual_subject_uses(event.subject_ref, available):
            add(ref, at_chapter=use_chapters.get(ref.key(), event.chapter_ordinal))
        resolution = decisions[event.obligation_id].resolution
        if resolution.kind == 'unresolved':
            raise RefusedByStructure('必要知识关联尚未闭合，不能省略后继续审阅')
        if event.chapter_ordinal == ordinal:
            for ref in resolution.subject_refs:
                add(ref)
        for presented in resolution.identity_actors:
            actor(presented, event.chapter_ordinal)
        if event.audience and event.audience.kind == 'character':
            actor(event.audience.actor, event.chapter_ordinal)
    for segment in bp.narration:
        for ref in (segment.narrator, segment.focalizer):
            if ref:
                actor(ref)
    for constraint in bp.constraints:
        if getattr(constraint, 'observer', None):
            actor(constraint.observer)
    known = {}
    for chapter in bps.values():
        for kind, rows, field in (('beat', chapter.beats, 'beat_id'),
                                   ('state', chapter.states, 'state_id')):
            for row in rows:
                known[kind, getattr(row, field)] = chapter, row.model_dump(mode='json')
    global_raw = {
        **{('character', ref): row for ref, row in characters['characters'].items()},
        **{('relation', row['relation_id']): row for row in grounding['relations']},
        **{('world_rule', rule['rule_id'] + '/' + version['version_id']):
               {'kind': rule['kind'], **version}
           for rule in grounding['world_rules'] for version in rule['versions']},
        **{('secret', row['secret_id']): row for row in grounding['arc_secrets']},
        **{('setting', field): {'value': grounding['setting'][field],
                               'evidence_refs': grounding['setting'][field + '_evidence_refs']}
           for field in ('era', 'social_context') if grounding['setting'][field] is not None},
    }
    # 闭包按带呈现的完整引用保存；不能以 (kind, char_id) 消掉另一表面身份。
    examined = set()
    while set(needed) - examined:
        for identity in sorted(set(needed) - examined):
            examined.add(identity)
            ref = needed[identity]
            scope = use_chapters[identity]
            key = ref.raw_subject_key()
            if ref.actor:
                actor(ref.actor, scope)
            if ref.relation_actors:
                relation = global_raw.get(key)
                if relation is None or (ref.relation_actors.subject.char_id,
                        ref.relation_actors.object.char_id) != (
                        relation['subject_id'], relation['object_id']):
                    raise RefusedByStructure('关系呈现端点与登记关系不一致')
                actor(ref.relation_actors.subject, scope)
                actor(ref.relation_actors.object, scope)
            if key in known:
                _chapter, value = known[key]
                owner_scope = _chapter.chapter_ordinal
                if value.get('global_ref'):
                    add(value['global_ref'], at_chapter=owner_scope)
                if value.get('holder'):
                    actor(value['holder'], owner_scope)
                for participant in value.get('participants', []):
                    actor(participant, owner_scope)
                for context in value.get('source_contexts', []):
                    for present in context['present'] or []:
                        actor(present, owner_scope)
                for prior in value.get('supersedes_state_refs', []):
                    add('state', prior)
            if key[0] == 'secret' and key in global_raw:
                target = global_raw[key]['fact_ref']
                if target and target['kind'] == 'world_rule_version':
                    add('world_rule', target['rule_id'] + '/' + target['version_id'])
    for ref in tuple(needed.values()):
        key = ref.raw_subject_key()
        if key[0] == 'secret' and key in global_raw:
            target = global_raw[key]['fact_ref']
            if target and target['kind'] == 'relation':
                matches = [r for r in needed.values()
                           if r.raw_subject_key() == ('relation', target['relation_id'])]
                if len(matches) != 1:
                    raise RefusedByStructure('秘密引用关系缺少唯一显式呈现，交 Blueprint owner 订正')
    evidence, requested = {}, {ordinal: [(0, len(texts[ordinal]))]}

    def anchor(chapter, start, end, ref):
        if chapter not in texts or not 0 <= start < end <= len(texts[chapter]):
            raise RefusedByStructure('审阅证据不在当前源文内')
        row = {'evidence_ref': ref, 'chapter_ordinal': chapter, 'start': start, 'end': end}
        if ref in evidence and evidence[ref] != row:
            raise RefusedByStructure('同一证据引用出现不同坐标')
        evidence[ref] = row
        requested.setdefault(chapter, []).append((start, end))
        return ref

    def blueprint_proof(chapter, refs):
        table = {r.evidence_id: r for r in chapter.evidence_anchors}
        for ref in refs:
            if ref not in table:
                raise RefusedByStructure('必要 Blueprint 证据缺失')
            row = table[ref]
            anchor(chapter.chapter_ordinal, row.start, row.end, ref)

    def project(value: Any, owner: str) -> Any:
        if isinstance(value, list):
            return [project(row, owner) for row in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key.endswith('evidence_refs'):
                refs = []
                table = (characters if owner == 'characters' else grounding)['evidence_anchors']
                for ref in item:
                    if ref not in table:
                        raise RefusedByStructure('必要 Core 证据引用缺失')
                    proof = table[ref]
                    number, start, end = proof['chapter_ordinal'], proof['start'], proof['end']
                    # 先范围验证，再核验实际下发的原文字节；两个 owner 的同名证据独立。
                    issued = anchor(number, start, end, f'{owner}:{ref}')
                    if hashlib.sha256(texts[number][start:end].encode('utf-8')).hexdigest() != proof['text_sha256']:
                        raise RefusedByStructure('Core 证据与当前源文字节不一致')
                    refs.append(issued)
                result[key] = refs
            else:
                result[key] = project(item, owner)
        return result

    registered = {r.event_id: r.event for r in links.scope.registered_events if not r.retired}
    for event in events:
        declaration = registered.get(event.knowledge_ref)
        if declaration is None:
            continue
        for proof in declaration.evidence_refs:
            chapter = bps[proof.chapter_ordinal]
            if any(r.evidence_id == proof.evidence_ref for r in chapter.evidence_anchors):
                blueprint_proof(chapter, [proof.evidence_ref])
            else:
                from novel.stages.stage2.paragraphs import span_of
                _, start_para, end_para = proof.evidence_ref.split(':')
                start, end = span_of(texts[proof.chapter_ordinal], int(start_para), int(end_para))
                anchor(proof.chapter_ordinal, start, end, proof.evidence_ref)
    for binding in bindings.values():
        owner = next((chapter for chapter in bps.values()
                      if any(a.presentation_id == binding['presentation_id'] for a in chapter.actor_bindings)), None)
        if owner is None:
            raise RefusedByStructure('呈现绑定缺少正式拥有章')
        blueprint_proof(owner, binding['evidence_refs'])
    support, globals_ = [], []
    for identity, ref in sorted(needed.items()):
        key = ref.raw_subject_key()
        if key in known:
            chapter, value = known[key]
            if chapter.chapter_ordinal != ordinal:
                blueprint_proof(chapter, value['evidence_refs'])
                support.append({'subject_ref': ref.model_dump(mode='json'), 'value': copy.deepcopy(value)})
        elif key[0] not in ('constraint', 'chapter_boundary'):
            if key not in global_raw:
                raise RefusedByStructure(f'审阅缺少必要共享主体：{key}')
            globals_.append({'subject_ref': ref.model_dump(mode='json'),
                'value': project(global_raw[key], 'characters' if key[0] == 'character' else 'global')})
    sources = []
    for chapter, ranges in sorted(requested.items()):
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(end, merged[-1][1])
            else:
                merged.append((start, end))
        source = texts[chapter]
        sources.append({'chapter_ordinal': chapter, 'ranges': [
            {'start': start, 'end': end, **({'text': source[start:end]} if materialize else
                {'text_sha256': hashlib.sha256(source[start:end].encode('utf-8')).hexdigest()})}
            for start, end in merged]})
    return {'contract': 'plot-chapter-evidence-v2', 'blueprint': bp.model_dump(mode='json'),
        'support_subjects': support, 'actor_bindings': [bindings[k] for k in sorted(bindings)],
        'global_subjects': globals_, 'source_context_catalog': source_context_catalog(bp),
        'knowledge_events': [{'event': r.model_dump(mode='json', exclude={
                'candidate_subject_refs', 'entry_subject_refs', 'candidate_actors', 'subject_sha256'}),
            'resolution': decisions[r.obligation_id].resolution.model_dump(mode='json')} for r in events],
        'evidence_anchors': [evidence[k] for k in sorted(evidence)], 'source_materials': sources}


def chapter_materials(ordinal: int, blueprints: dict, grounding: dict,
                      knowledge_links: dict, main_text: list[dict],
                      subject_refs: list[dict], *, characters: dict) -> PlotChapterReviewMaterials:
    return PlotChapterReviewMaterials.model_validate(_compile_materials(
        ordinal, blueprints, grounding, knowledge_links, main_text, subject_refs,
        characters=characters, materialize=True))


def chapter_materials_identity(ordinal: int, blueprints: dict, grounding: dict,
                               knowledge_links: dict, main_text: list[dict],
                               subject_refs: list[dict], *, characters: dict) -> str:
    """轻量依赖摘要复用同一闭包，仅散列实际下发的证据字节。"""
    from novel.consumption import consumed_digest
    return consumed_digest(_compile_materials(
        ordinal, blueprints, grounding, knowledge_links, main_text, subject_refs,
        characters=characters, materialize=False))


def material_identity(material: PlotChapterReviewMaterials) -> str:
    """验证下发正文与轻量依赖摘要一致，不能两条公式各自漂移。"""
    from novel.consumption import consumed_digest
    payload = material.model_dump(mode='json')
    for source in payload['source_materials']:
        for row in source['ranges']:
            row['text_sha256'] = hashlib.sha256(row.pop('text').encode('utf-8')).hexdigest()
    return consumed_digest(payload)
