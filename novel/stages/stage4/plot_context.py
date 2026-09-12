"""WriterVisibleView V9：内部实体与公开表面身份分离的唯一编译器。

整章请求仍看得到后场材料，表达受显式场景许可约束；代码不重写自由文本中的
身份真相，语义保真由 Commander 审核。没有明确呈现或知识端点时拒绝并保留 owner 修复入口。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from contracts.schema.knowledge_links import KnowledgeLinks
from contracts.schema.plot import PlotBlueprint, PlotSubjectRef
from contracts.schema.stage4 import CtxChapter
from novel.core.text import norm
from novel.errors import RefusedByStructure
from novel.stages.stage4.actors import ActorCatalog, actual_subject_uses
from novel.stages.stage4.plot_binding import source_context_catalog
from novel.stages.stage4.plot_placements import compile_plan_placements, knowledge_triggers_for_chapter
from novel.stages.stage4.plot_refs import global_subjects, validate_plan


def build_writer_context(plan: dict[str, Any], global_g: dict[str, Any],
                         blueprints: dict[str, Any], main_text: list[dict[str, Any]], *,
                         knowledge_links: dict[str, Any], characters: dict[str, Any]):
    from novel.stages.stage4.context import Assembly
    from novel.stages.stage4.measure import semantic_contract_of

    validate_plan(plan, blueprints, global_g, knowledge_links, characters=characters, main_text=main_text)
    links = KnowledgeLinks.model_validate(knowledge_links)
    records = {row.obligation_id: row for row in links.links}
    bps = {row['chapter_ordinal']: PlotBlueprint.model_validate(row) for row in blueprints['blueprints']}
    states = {state.state_id: state for bp in bps.values() for state in bp.states}
    all_beats = {beat.beat_id: beat for bp in bps.values() for beat in bp.beats}
    state_origins = {state.state_id: bp.chapter_ordinal for bp in bps.values() for state in bp.states}
    state_endings = {}
    for source_bp in bps.values():
        for state in source_bp.states:
            if state.ended_at is not None:
                state_endings[state.state_id] = source_bp.chapter_ordinal
            for ref in state.supersedes_state_refs:
                state_endings[ref] = source_bp.chapter_ordinal
        for ending in source_bp.inherited_state_endings:
            state_endings[ending.state_ref] = source_bp.chapter_ordinal
    beat_origins = {beat.beat_id: bp.chapter_ordinal for bp in bps.values() for beat in bp.beats}
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    seed = hashlib.sha256(json.dumps(texts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    globals_ = global_subjects(global_g, characters=characters)
    catalogs = {}
    def catalog_at(number):
        if number not in catalogs:
            catalogs[number] = ActorCatalog(characters, blueprints, chapter_ordinal=number,
                source_text='\n'.join(texts[o] for o in sorted(texts) if o <= number), seed=seed)
        return catalogs[number]
    asm, result = Assembly(), []
    for chapter in plan['output_chapters']:
        ordinal = chapter['output_ordinal']
        bp = bps[ordinal]
        actors = catalog_at(ordinal)
        compiled = compile_plan_placements(bp, chapter, knowledge=knowledge_triggers_for_chapter(knowledge_links, ordinal))
        slots = [scene['slot'] for scene in chapter['scenes']]
        positions = {slot: i for i, slot in enumerate(slots)}
        endpoints = {row.key(): row.scene_ref for row in compiled.bindings}
        contexts = source_context_catalog(bp)
        obligations = [row for row in links.scope.obligations if row.chapter_ordinal == ordinal]
        state_ids = set(bp.entry_state_refs) | {s.state_id for s in bp.states}
        state_ids.update(row.subject_ref.ref for row in obligations if row.subject_ref.kind == 'state')
        hidden = {ref for ref in state_ids if any(row.kind == 'reader_reveal'
            and row.subject_ref.kind == 'state' and row.subject_ref.ref == ref and row.chapter_ordinal > ordinal
            for row in links.scope.obligations)}
        visible_states = [states[ref] for ref in sorted(state_ids - hidden)]
        background = {row.subject_ref.ref for row in obligations if row.subject_ref.kind == 'beat'
                      and row.subject_ref.ref not in {b.beat_id for b in bp.beats}}
        needed = {}
        subject_catalogs = {}

        def normalize(ref, source_catalog=None):
            selected = source_catalog or actors
            ref = PlotSubjectRef.model_validate(ref.model_dump(mode='json') if hasattr(ref, 'model_dump') else ref)
            data = ref.model_dump(mode='json')
            if ref.actor:
                data['actor'] = selected.resolve(ref.actor).model_dump(mode='json')
            if ref.relation_actors:
                data['relation_actors'] = {
                    'subject': selected.resolve(ref.relation_actors.subject).model_dump(mode='json'),
                    'object': selected.resolve(ref.relation_actors.object).model_dump(mode='json')}
            return PlotSubjectRef.model_validate(data)

        def need(ref, source_catalog=None):
            selected = source_catalog or actors
            ref = normalize(ref, selected)
            needed[ref.key()] = ref
            subject_catalogs.setdefault(ref.key(), selected)

        def ref_catalog(ref):
            return subject_catalogs.get(ref.key(), actors)

        def public_ref(ref):
            return ref_catalog(ref).subject(ref, public_catalog=actors)

        for ref in bp.relation_uses:
            need(ref)
        for ref in state_ids:
            need({'kind': 'state', 'ref': ref})
        for state in visible_states:
            if state.global_ref:
                need(state.global_ref, catalog_at(state_origins[state.state_id]))
        for name in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
            for ref in chapter[name]['subject_refs']:
                need(ref)
        for row in obligations:
            if row.subject_ref.kind != 'relation':
                need(row.subject_ref.model_dump())

        def event_slots(row):
            if row.chapter_ordinal > ordinal:
                return []
            record = records.get(row.obligation_id)
            if record is None or record.resolution.kind == 'unresolved':
                raise RefusedByStructure('未解决知识事件不能编译写作权限')
            if row.chapter_ordinal < ordinal or record.resolution.kind == 'at_entry':
                return list(slots)
            slot = endpoints.get(('knowledge', row.obligation_id, 'trigger'))
            if slot is None:
                raise RefusedByStructure('知识事件缺少明确场景端点')
            return slots[positions[slot]:]

        def permission_row(kind, ref, *, revealed_from=None):
            rows = [row for row in links.scope.obligations if row.kind == kind and row.knowledge_ref == ref]
            if len(rows) != 1:
                raise RefusedByStructure(f'知识事件未唯一登记：{kind}:{ref}')
            if revealed_from is not None and rows[0].chapter_ordinal != revealed_from:
                raise RefusedByStructure('知识披露端点与 Global revealed_from 不一致')
            return rows[0]

        def permission(kind, ref, *, revealed_from=None):
            return event_slots(permission_row(kind, ref, revealed_from=revealed_from))

        def endpoint_tokens(ref, source_catalog):
            if ref.relation_actors is None:
                raise RefusedByStructure('关系披露材料缺少明确表面身份端点')
            return tuple(source_catalog.token(a, public_catalog=actors)
                         for a in (ref.relation_actors.subject, ref.relation_actors.object))

        relation_bases = {}
        def relation_basis(row, relation_ref):
            """从已接受的支持材料取公开关系 use，不把内部默认端点当作呈现决定。"""
            cache_key = (row.obligation_id, relation_ref)
            if cache_key in relation_bases:
                return relation_bases[cache_key]
            resolution = records[row.obligation_id].resolution
            direct, candidates, supported_actors = set(), set(), set()
            source_chapters = set()
            for support in resolution.subject_refs:
                if support.kind == 'state':
                    state = states[support.ref]
                    source_chapters.add(state_origins[support.ref])
                    if state.global_ref and state.global_ref.raw_subject_key() == ('relation', relation_ref):
                        direct.add(endpoint_tokens(state.global_ref, catalog_at(state_origins[support.ref])))
                elif support.kind == 'beat':
                    number = beat_origins[support.ref]
                    source_chapters.add(number)
                    source_catalog = catalog_at(number)
                    supported_actors.update(source_catalog.token(a, public_catalog=actors)
                        for a in all_beats[support.ref].participants)
            if direct:
                # 多个显式 State 可以各自证明一种公开呈现；不是从候选中择一。
                relation_bases[cache_key] = direct
                return direct
            for number in source_chapters:
                source_bp, source_catalog = bps[number], catalog_at(number)
                uses = [*source_bp.relation_uses,
                        *(s.global_ref for s in source_bp.states if s.global_ref)]
                candidates.update(endpoint_tokens(ref, source_catalog) for ref in uses
                    if ref.raw_subject_key() == ('relation', relation_ref))
            supported = {pair for pair in candidates if set(pair) <= supported_actors}
            if len(supported) == 1:
                candidates = supported
            if len(candidates) != 1:
                raise RefusedByStructure('关系披露支持材料缺少唯一明确的公开端点；须由 Blueprint/KnowledgeLinks owner 免费订正')
            relation_bases[cache_key] = candidates
            return candidates

        # 只使用本次已有、已接受的身份披露端点；不保存新的身份图或补猜映射。
        identity_permissions = []
        for identity_row in links.scope.obligations:
            if identity_row.chapter_ordinal > ordinal:
                continue
            record = records.get(identity_row.obligation_id)
            if record is None or record.resolution.kind == 'unresolved' or not record.resolution.identity_actors:
                continue
            source_catalog = catalog_at(identity_row.chapter_ordinal)
            source_catalog.identity_event(record.resolution.identity_actors,
                chapter_ordinal=identity_row.chapter_ordinal, kind=identity_row.kind)
            identity_permissions.append((set(event_slots(identity_row)), {
                source_catalog.token(a, public_catalog=actors) for a in record.resolution.identity_actors}))

        def relation_slots(ref, allowed, disclosure):
            if not allowed:
                return []
            target = endpoint_tokens(ref, ref_catalog(ref))
            bases = relation_basis(disclosure, ref.ref)
            def connected(left, right, slot):
                reached = {left}
                while right not in reached:
                    enlarged = reached | {token for permitted, members in identity_permissions
                        if slot in permitted and members & reached for token in members}
                    if enlarged == reached:
                        return False
                    reached = enlarged
                return True
            return [slot for slot in allowed if any(
                all(connected(before, after, slot) for before, after in zip(basis, target)) for basis in bases)]

        actor_uses = {}
        def actor(value, allowed, source_catalog=None):
            if value is None:
                return None
            selected = source_catalog or actors
            resolved = selected.resolve(value)
            token = selected.token(resolved, public_catalog=actors)
            entries = actor_uses.setdefault(token, {})
            entry = entries.setdefault((selected.ordinal, resolved.key()), [selected, resolved, set()])
            entry[2].update(allowed)
            return token

        def public_subject(ref, allowed):
            ref = PlotSubjectRef.model_validate(ref.model_dump(mode='json') if hasattr(ref, 'model_dump') else ref)
            selected = ref_catalog(ref)
            if ref.actor:
                actor(ref.actor, allowed, selected)
            if ref.relation_actors:
                actor(ref.relation_actors.subject, allowed, selected)
                actor(ref.relation_actors.object, allowed, selected)
            return selected.subject(ref, public_catalog=actors)

        def relation_value(ref, value, allowed):
            if ref.relation_actors is None:
                raise RefusedByStructure('关系消费缺少明确两端表面身份')
            ends = ref.relation_actors
            if ends.subject.char_id != value['subject_id'] or ends.object.char_id != value['object_id']:
                raise RefusedByStructure('关系呈现端点与知识底稿不一致')
            selected = ref_catalog(ref)
            return {'subject': actor(ends.subject, allowed, selected), 'predicate': value['predicate'],
                    'object': actor(ends.object, allowed, selected), 'effective_from': value['effective_from'],
                    'effective_until': value['effective_until'], 'revealed_from': value['revealed_from']}

        def secret_relation(value):
            target = value['fact_ref']
            candidates = [ref for ref in needed.values() if ref.kind == 'relation'
                          and ref.ref == target['relation_id']]
            by_use = {public_ref(ref)['ref']: ref for ref in candidates}
            if len(by_use) != 1:
                raise RefusedByStructure('秘密引用关系需要唯一显式关系呈现；交 Blueprint/Plan owner 订正')
            return next(iter(by_use.values()))

        def secret_value(value, allowed):
            if value['fact'] is not None:
                return {'fact': value['fact']}
            target = value['fact_ref']
            if target['kind'] == 'relation':
                ref = secret_relation(value)
                return {'fact': relation_value(ref, globals_['relation', ref.ref], allowed)}
            version = globals_.get(('world_rule', target['rule_id'] + '/' + target['version_id']))
            if version is None:
                raise RefusedByStructure('秘密引用规则版本缺失')
            return {'fact': {k: version[k] for k in
                            ('desc', 'effective_from', 'effective_until', 'revealed_from')}}

        def secret_relation_slots(value, allowed):
            ref = secret_relation(value)
            relation = globals_['relation', ref.ref]
            disclosure = permission_row('relation_visibility', ref.ref, revealed_from=relation['revealed_from'])
            visible = event_slots(disclosure)
            # fact_ref 只去除同义重复；秘密自己的支持不能替换关系已审的公开端点。
            return relation_slots(ref, [s for s in allowed if s in visible], disclosure)


        state_slots = {}
        for state in visible_states:
            reveals = [r for r in links.scope.obligations if r.kind == 'reader_reveal'
                       and r.subject_ref.kind == 'state' and r.subject_ref.ref == state.state_id]
            if len(reveals) > 1:
                raise RefusedByStructure('同一状态不能有两套读者披露时间线')
            if reveals:
                state_slots[state.state_id] = event_slots(reveals[0])
            elif state.state_id in bp.entry_state_refs or state.established_at.kind == 'at_entry':
                state_slots[state.state_id] = list(slots)
            else:
                slot = endpoints.get(('state', state.state_id, 'established'))
                if slot is None:
                    raise RefusedByStructure('状态没有建立端点')
                state_slots[state.state_id] = slots[positions[slot]:]

        # 命题被允许作为历史表达，不代表可继续作为人物当前公开身份。
        # 角色描述同时服从公开许可与已有 State 成立/解除端点；解除场仍包含在窗口内。
        role_state_slots = {}
        for state in visible_states:
            permitted = list(state_slots[state.state_id])
            if state_origins[state.state_id] == ordinal and state.established_at.kind == 'at_beat':
                start = endpoints.get(('state', state.state_id, 'established'))
                if start is None:
                    raise RefusedByStructure('公开角色状态缺少成立端点')
                permitted = [slot for slot in permitted if positions[slot] >= positions[start]]
            ended = state_endings.get(state.state_id)
            if ended is not None and ended < ordinal:
                permitted = []
            elif ended == ordinal:
                end = endpoints.get(('state', state.state_id, 'ended'))
                if end is None:
                    raise RefusedByStructure('公开角色状态缺少解除端点')
                permitted = [slot for slot in permitted if positions[slot] <= positions[end]]
            role_state_slots[state.state_id] = permitted

        global_rows = []
        restricted = [{'kind': 'state', 'ref': ref} for ref in sorted(hidden)]
        for ref in sorted(needed.values(), key=lambda r: r.key()):
            if ref.kind in ('beat', 'state', 'constraint', 'chapter_boundary', 'character'):
                continue
            value = globals_.get(ref.raw_subject_key())
            if value is None:
                raise RefusedByStructure(f'共享主体不存在：{ref.kind}:{ref.ref}')
            visible, factual = None, []
            if ref.kind == 'secret':
                factual = permission('secret_reveal', ref.ref, revealed_from=value['revealed_from'])
                if factual and value['fact_ref'] and value['fact_ref']['kind'] == 'relation':
                    factual = secret_relation_slots(value, factual)
                if factual:
                    visible = secret_value(value, factual)
            elif ref.kind == 'relation':
                factual = permission('relation_visibility', ref.ref, revealed_from=value['revealed_from'])
                factual = relation_slots(ref, factual, permission_row('relation_visibility', ref.ref))
                if factual:
                    visible = relation_value(ref, value, factual)
            elif ref.kind == 'world_rule':
                factual = permission('world_rule_reveal', ref.ref, revealed_from=value['revealed_from'])
                if factual:
                    visible = {k: value[k] for k in
                               ('kind', 'desc', 'effective_from', 'effective_until', 'revealed_from')}
            elif ref.kind == 'setting':
                factual = permission('setting', ref.ref)
                visible = {'content': value} if factual else None
            state_permissions = []
            for state in visible_states:
                if not state.global_ref or normalize(state.global_ref,
                        catalog_at(state_origins[state.state_id])).key() != ref.key():
                    continue
                permitted = state_slots[state.state_id]
                if state.modality == 'world':
                    if not factual or not set(permitted) <= set(factual):
                        raise RefusedByStructure('客观状态不能越过引用命题的读者披露许可')
                elif permitted:
                    reveals = [r for r in links.scope.obligations if r.kind == 'reader_reveal'
                               and r.subject_ref.kind == 'state' and r.subject_ref.ref == state.state_id]
                    if not reveals:
                        raise RefusedByStructure('未开放命题的信念/声称必须登记必要读者披露')
                    state_permissions.append({'state_ref': state.state_id, 'modality': state.modality,
                                              'allowed_slots': permitted})
            all_allowed = [s for s in slots if s in factual or any(s in p['allowed_slots'] for p in state_permissions)]
            if not all_allowed:
                # 不为仅隐藏的关系创建端点人物；只有 opaque 命题身份。
                restricted.append(public_ref(ref))
                continue
            if visible is None:
                if ref.kind == 'relation':
                    visible = relation_value(ref, value, all_allowed)
                elif ref.kind == 'secret':
                    visible = secret_value(value, all_allowed)
                elif ref.kind == 'setting':
                    visible = {'content': value}
                else:
                    raise RefusedByStructure('状态引用规则需要明确有据的版本')
            global_rows.append({'subject_ref': public_subject(ref, all_allowed), 'value': visible,
                                'allowed_slots': factual, 'state_permissions': state_permissions})

        public_globals = {}
        for row in global_rows:
            key = (row['subject_ref']['kind'], row['subject_ref']['ref'])
            prior = public_globals.setdefault(key, row)
            if prior is row:
                continue
            if prior['value'] != row['value']:
                raise RefusedByStructure('同一公开关系呈现对应不同命题正文；不能择一')
            prior['allowed_slots'] = [s for s in slots if s in prior['allowed_slots'] or s in row['allowed_slots']]
            prior['state_permissions'] = list({p['state_ref']: p for p in
                [*prior['state_permissions'], *row['state_permissions']]}.values())
        global_rows = list(public_globals.values())
        restricted = list({(r['kind'], r['ref']): r for r in restricted
                           if (r['kind'], r['ref']) not in public_globals}.values())

        events = []
        for row in links.scope.obligations:
            actual_refs = actual_subject_uses(row.subject_ref, needed.values())
            actual_refs = [ref for ref in actual_refs if ref.key() in needed]
            if not actual_refs:
                continue
            allowed = event_slots(row)
            if not allowed:
                continue
            resolution = records[row.obligation_id].resolution
            event_catalog = catalog_at(row.chapter_ordinal)
            event_catalog.identity_event(resolution.identity_actors, chapter_ordinal=row.chapter_ordinal, kind=row.kind)
            # 一个底稿关系可能有多个合法呈现；只扩展实际使用，不发内部端点目录。
            for ref in actual_refs:
                per_use = allowed
                if ref.kind == 'relation':
                    value = globals_[ref.raw_subject_key()]
                    disclosed = permission('relation_visibility', ref.ref, revealed_from=value['revealed_from'])
                    per_use = relation_slots(ref, [s for s in allowed if s in disclosed],
                                             permission_row('relation_visibility', ref.ref))
                elif ref.kind == 'secret' and row.kind == 'secret_reveal':
                    value = globals_[ref.raw_subject_key()]
                    if value['fact_ref'] and value['fact_ref']['kind'] == 'relation':
                        per_use = secret_relation_slots(value, allowed)
                if not per_use:
                    continue
                audience = None
                if row.audience:
                    audience = ({'kind': 'character', 'actor_ref': actor(row.audience.actor, per_use, event_catalog)}
                                if row.audience.kind == 'character' else row.audience.model_dump(mode='json'))
                identity = list(dict.fromkeys(actor(a, per_use, event_catalog) for a in resolution.identity_actors))
                if len(identity) < 2:
                    identity = []  # 已在前章公开为同一呈现，不输出“自己等于自己”。
                at_entry = (row.chapter_ordinal < ordinal or resolution.kind == 'at_entry') and per_use[0] == slots[0]
                public_subject_ref = public_ref(ref)
                event_ref = actors.event(row.obligation_id, public_subject_ref)
                events.append({'event_ref': event_ref, 'kind': row.kind,
                    'subject_ref': public_subject_ref, 'audience': audience,
                    'identity_actors': identity, 'timing': 'at_entry' if at_entry else 'at_slot',
                    'slot': None if at_entry else per_use[0], 'allowed_slots': per_use})

        public_events = {}
        for event in events:
            prior = public_events.setdefault(event['event_ref'], event)
            if prior is not event and prior != event:
                raise RefusedByStructure('同一公开知识事件有不同许可；须由 KnowledgeLinks owner 订正')
        events = list(public_events.values())

        goals = {}
        restricted_keys = {(r['kind'], r['ref']) for r in restricted}
        for name in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
            goal = chapter[name]
            normalized = [normalize(ref) for ref in goal['subject_refs']]
            refs = [public_ref(ref) if (public_ref(ref)['kind'], public_ref(ref)['ref']) in restricted_keys
                    else public_subject(ref, [goal['anchor_scene_ref']]) for ref in normalized]
            if any((r['kind'], r['ref']) in restricted_keys for r in refs) and goal['intent'] != 'withhold':
                raise RefusedByStructure('目标引用未开放命题，只能保留未知')
            for event in events:
                if (event['subject_ref'] in refs and event['kind'] in
                    ('secret_reveal', 'relation_visibility', 'world_rule_reveal', 'reader_reveal')
                    and goal['intent'] != 'withhold' and goal['anchor_scene_ref'] not in event['allowed_slots']):
                    raise RefusedByStructure('目标端点早于必要披露许可')
            goals[name] = {**goal, 'subject_refs': refs}

        def beat_slots(ref):
            return [s for s in slots if any(p['beat_ref'] == ref and any(d['scene_ref'] == s
                for d in p['deliveries']) for p in chapter['beat_placements'])]

        beats = [{'beat_ref': b.beat_id, 'content': b.content,
                  'participants': list(dict.fromkeys(actor(a, beat_slots(b.beat_id)) for a in b.participants))} for b in bp.beats]
        background_rows = [{'beat_ref': ref, 'content': all_beats[ref].content,
            'participants': list(dict.fromkeys(actor(a, slots, catalog_at(beat_origins[ref]))
                             for a in all_beats[ref].participants))} for ref in sorted(background)]
        state_rows = [{'state_ref': s.state_id, 'content': s.content,
            'global_ref': public_subject(normalize(s.global_ref, catalog_at(state_origins[s.state_id])),
                state_slots[s.state_id]) if s.global_ref else None,
            'modality': s.modality, 'holder': actor(s.holder, state_slots[s.state_id],
                catalog_at(state_origins[s.state_id]))} for s in visible_states]
        constraints = []
        for c in bp.constraints:
            row = c.model_dump(exclude={'constraint_id', 'evidence_refs'}, mode='json')
            if 'observer' in row:
                row['observer'] = actor(c.observer, beat_slots(c.after_beat_ref))
            constraints.append({'constraint_ref': c.constraint_id, **row})
        scenes = []
        for scene in chapter['scenes']:
            slot = scene['slot']
            deliveries = []
            for p in chapter['beat_placements']:
                for d in p['deliveries']:
                    if d['scene_ref'] != slot:
                        continue
                    stages = []
                    for ref in d['source_context_refs']:
                        stage = contexts[ref]
                        stages.append({'time_layer': stage['time_layer'], 'place': stage['place'],
                            'present': None if stage['present'] is None else list(dict.fromkeys(actor(a, [slot]) for a in stage['present']))})
                    deliveries.append({'beat_ref': p['beat_ref'],
                        'presentation_instruction': d['presentation_instruction'], 'stages': stages})
            scenes.append({**scene, 'deliveries': deliveries,
                'state_events': [{'state_ref': r.ref, 'endpoint': r.endpoint} for r in compiled.bindings
                                 if r.kind == 'state' and r.scene_ref == slot],
                'knowledge_event_refs': [e['event_ref'] for e in events if slot in e['allowed_slots']],
                'constraint_refs': sorted({r.ref for r in compiled.bindings if r.kind == 'constraint' and r.scene_ref == slot})})
        narration = []
        for i, segment in enumerate(bp.narration):
            allowed = [slot for slot in slots if not segment.beat_refs
                       or any(slot in beat_slots(ref) for ref in segment.beat_refs)]
            narration.append({'segment': '全章' if len(bp.narration) == 1 else f'第{i+1}段',
                'grammatical_person': segment.grammatical_person, 'narrator': actor(segment.narrator, allowed),
                'focalizer': actor(segment.focalizer, allowed), 'pronoun_surfaces': segment.pronoun_surfaces,
                'beat_refs': segment.beat_refs, 'allowed_slots': allowed})
        roster = []
        previous_source = norm('\n'.join(texts[number] for number in sorted(texts) if number < ordinal))
        anchor_ranges = {a.evidence_id: a for a in bp.evidence_anchors}
        def surface_slots(surface):
            # 称谓能出现与其真实归属已揭示是两回事；只按已出现原文及实际 Beat 呈现定位。
            if norm(surface) in previous_source:
                return set(slots)
            support = [slot for beat in bp.beats if any(norm(surface) in norm(
                texts[ordinal][anchor_ranges[ref].start:anchor_ranges[ref].end])
                for ref in beat.evidence_refs) for slot in beat_slots(beat.beat_id)]
            if not support:
                raise RefusedByStructure('当章人物称谓缺少可落场的源文节点支持；须由 Blueprint owner 免费订正')
            first = min(positions[slot] for slot in support)
            return set(slots[first:])

        for token, entries in sorted(actor_uses.items()):
            descriptions = [source_catalog.describe(a, allowed_slots=[s for s in slots if s in allowed],
                public_states=role_state_slots) for source_catalog, a, allowed in entries.values() if allowed]
            surfaces = list(dict.fromkeys(s for d in descriptions for s in d['surfaces']))
            roles = {d['role'] for d in descriptions if d['role'] is not None}
            if len(roles) > 1:
                raise RefusedByStructure('同一公开人物有多个不同角色描述；须由 Blueprint owner 明确当前公开状态，不能取首项')
            permitted_surfaces = {name: [s for s in slots if s in surface_slots(name) and any(
                s in d['surface_allowed_slots'].get(name, []) for d in descriptions)] for name in surfaces}
            if any(not allowed for allowed in permitted_surfaces.values()):
                raise RefusedByStructure('人物称谓支持场与实际人物呈现无交集；须由 Blueprint owner 免费订正')
            roster.append({'actor_ref': token, 'display_name': surfaces[0], 'surfaces': surfaces,
                'surface_allowed_slots': permitted_surfaces,
                'allowed_slots': [s for s in slots if any(s in d['allowed_slots'] for d in descriptions)],
                'role': next(iter(roles)) if roles else None,
                'role_allowed_slots': [s for s in slots if any(s in d['role_allowed_slots'] for d in descriptions)]})
        row = {'output_ordinal': ordinal, **goals, 'chapter_rewrite_brief': chapter['chapter_rewrite_brief'],
            'semantic_contract': semantic_contract_of(bp.model_dump(mode='json')),
            'beats': beats, 'background_beats': background_rows, 'states': state_rows, 'constraints': constraints,
            'global_subjects': global_rows, 'restricted_subject_refs': restricted, 'knowledge_events': events,
            'scenes': scenes, 'characters': roster, 'narration': narration}
        # 结构字段通过严格载体验证；自由文本夹带正式实体 ID 同样不能越过边界。
        payload = json.dumps(row, ensure_ascii=False)
        if any(cid in payload for cid in characters['characters']):
            raise RefusedByStructure('写作材料自由文本夹带内部人物 ID；须由原 owner 免费订正')
        result.append(CtxChapter.model_validate(row).model_dump(mode='json'))
    asm.ctx = {'chapters': result}
    return asm
