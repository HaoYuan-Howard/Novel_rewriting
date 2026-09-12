"""WriterVisibleView 的唯一编译器：章级正文目录、显式端点和逐场表达许可。

整章材料一次下发；逐场许可不是模型物理隔离。这里不裁剪自由文本中的秘密，
也不以证据命中证明剧情完整。语义保真仍由设计、结构与成稿审阅承担。
"""
from __future__ import annotations

from typing import Any
from contracts.schema.knowledge_links import KnowledgeLinks
from contracts.schema.plot import PlotBlueprint
from contracts.schema.stage4 import CtxChapter
from novel.core.naming import (
    display_names_at, name_collisions, project_character_names,
    person_naming_surfaces, rule_names_a_person, surface_first_chapter,
)
from novel.errors import RefusedByStructure
from novel.stages.stage4.plot_binding import source_context_catalog
from novel.stages.stage4.plot_placements import (
    compile_plan_placements, knowledge_triggers_for_chapter,
)
from novel.stages.stage4.plot_refs import global_subjects, validate_plan


def _key(ref: dict) -> tuple[str, str]:
    return ref['kind'], ref['ref']


def build_writer_context(plan: dict[str, Any], global_g: dict[str, Any],
                         blueprints: dict[str, Any], main_text: list[dict[str, Any]], *,
                         knowledge_links: dict[str, Any]):
    from novel.stages.stage4.context import Assembly, _name_narration, rename_all

    validate_plan(plan, blueprints, global_g, knowledge_links)
    links = KnowledgeLinks.model_validate(knowledge_links)
    records = {row.obligation_id: row for row in links.links}
    bps = {row['chapter_ordinal']: PlotBlueprint.model_validate(row)
           for row in blueprints['blueprints']}
    states = {state.state_id: state for bp in bps.values() for state in bp.states}
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    characters = global_g['entities']['characters']
    naming, available_names = person_naming_surfaces(characters), surface_first_chapter(characters)
    globals_ = global_subjects(global_g)
    registry, subs, unattested = project_character_names(
        characters, '\n'.join(texts[o] for o in sorted(texts)))
    if unattested:
        raise RefusedByStructure(f'人物没有任何原文有据的可下发称呼：{unattested}')
    asm = Assembly(subs=subs)
    asm.collisions = name_collisions(
        {ref: row['display_name'] for ref, row in registry.items()}, characters)
    result = []
    for chapter in plan['output_chapters']:
        ordinal = chapter['output_ordinal']
        bp = bps[ordinal]
        compiled = compile_plan_placements(bp, chapter, knowledge=
            knowledge_triggers_for_chapter(knowledge_links, ordinal))
        slots = [scene['slot'] for scene in chapter['scenes']]
        positions = {slot: i for i, slot in enumerate(slots)}
        endpoints = {row.key(): row.scene_ref for row in compiled.bindings}
        contexts = source_context_catalog(bp)
        current_state_ids = set(bp.entry_state_refs) | {s.state_id for s in bp.states}
        current_states = [states[ref] for ref in sorted(current_state_ids)]
        needed_chars = {cid for beat in bp.beats for cid in beat.participant_char_ids}
        needed_chars.update(s.holder_char_id for s in current_states if s.holder_char_id)
        needed_chars.update(cid for row in contexts.values() for cid in row['present_char_ids'] or [])
        needed_chars.update(cid for segment in bp.narration
            for cid in (segment.narrator_char_id, segment.focalizer_char_id) if cid)
        needed_chars.update(c.observer_char_id for c in bp.constraints
                            if getattr(c, 'observer_char_id', None))
        needed = {('state', ref) for ref in current_state_ids}
        needed.update(_key(s.global_ref.model_dump()) for s in current_states if s.global_ref)
        for name in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
            needed.update(_key(ref) for ref in chapter[name]['subject_refs'])
        current_obligations = [row for row in links.scope.obligations
                               if row.chapter_ordinal == ordinal]
        needed.update((r.subject_ref.kind, r.subject_ref.ref) for r in current_obligations)
        for kind, ref in tuple(needed):
            if kind == 'character':
                needed_chars.add(ref)
            if kind == 'relation' and (kind, ref) in globals_:
                needed_chars.update(str(globals_[kind, ref][key])
                                    for key in ('subject_id', 'object_id'))
                visibility = globals_[kind, ref]['visibility']
                if visibility['kind'] == 'via_secret':
                    needed.add(('secret', visibility['secret_id']))
        for row in current_obligations:
            if row.audience is not None and row.audience.kind == 'character':
                needed_chars.add(row.audience.char_id)
        display_all = display_names_at(
            characters, '\n'.join(texts[o] for o in sorted(texts) if o <= ordinal))
        display = {ref: name for ref, name in display_all.items() if ref in needed_chars}
        asm.withheld.extend((ordinal, ref) for ref in sorted(needed_chars - display.keys()))
        asm.chapter_collisions.extend((ordinal, name, refs)
            for name, refs, _ in name_collisions(display, characters))

        def event_slots(row) -> list[str]:
            if row.chapter_ordinal > ordinal:
                return []
            resolution = records[row.obligation_id].resolution
            if row.chapter_ordinal < ordinal or resolution.kind == 'at_entry':
                return list(slots)
            if resolution.kind != 'at_beats':
                raise RefusedByStructure('未解决知识事件不能编译写作权限')
            slot = endpoints.get(('knowledge', row.obligation_id, 'trigger'))
            if slot is None:
                raise RefusedByStructure('知识事件缺少明确的场景端点')
            return slots[positions[slot]:]

        events = []
        for row in links.scope.obligations:
            if (row.subject_ref.kind, row.subject_ref.ref) not in needed:
                continue
            allowed = event_slots(row)
            if not allowed:
                continue
            resolution = records[row.obligation_id].resolution
            at_entry = row.chapter_ordinal < ordinal or resolution.kind == 'at_entry'
            events.append({
                'event_ref': row.obligation_id, 'kind': row.kind,
                'subject_ref': row.subject_ref.model_dump(mode='json'),
                'audience': row.audience.model_dump(mode='json') if row.audience else None,
                'timing': 'at_entry' if at_entry else 'at_slot',
                'slot': None if at_entry else allowed[0], 'allowed_slots': allowed,
            })

        def permission(kind: str, ref: str) -> list[str]:
            rows = [row for row in links.scope.obligations
                    if row.kind == kind and row.knowledge_ref == ref]
            if len(rows) != 1:
                raise RefusedByStructure(f'知识事件未唯一登记：{kind}:{ref}')
            return event_slots(rows[0])

        global_rows, restricted = [], []
        for kind, ref in sorted(needed):
            if kind in ('beat', 'state', 'constraint', 'chapter_boundary', 'character'):
                continue
            value = globals_.get((kind, ref))
            if value is None:
                raise RefusedByStructure(f'所需共享主体不存在：{kind}:{ref}')
            visible: Any = None
            factual_slots: list[str] = []
            if kind == 'secret':
                factual_slots = permission('secret_reveal', ref)
                visible = {'fact': value['fact']}
            elif kind == 'relation':
                visibility = value['visibility']
                reader = (permission('relation_visibility', ref) if visibility['kind'] == 'public'
                          else permission('secret_reveal', visibility['secret_id']))
                world = permission('relation', ref)
                valid_through = value['valid_through_chapter']
                if valid_through is not None and ordinal > valid_through:
                    world = []
                factual_slots = [slot for slot in slots if slot in world and slot in reader]
                visible = {key: value[key] for key in
                           ('subject_id', 'predicate', 'object_id',
                            'valid_from_chapter', 'valid_through_chapter')}
            elif kind == 'world_rule':
                versions = []
                active = max((v['effective_from_chapter'] for v in value['versions']
                              if v['effective_from_chapter'] <= ordinal), default=None)
                for version in value['versions']:
                    if version['effective_from_chapter'] != active:
                        continue
                    version_ref = f"{ref}/{version['effective_from_chapter']}"
                    world = permission('world_rule', version_ref)
                    reader = permission('world_rule_reveal', version_ref)
                    permitted = [slot for slot in slots if slot in world and slot in reader]
                    names = rule_names_a_person(version['desc'], naming)
                    if any(available_names.get(name, 1) > ordinal for name in names):
                        # 延用叫法权限：整条规则暂不下发，不裁剪秘密或另写可见正文。
                        asm.held_rules.add((ordinal, ref, tuple(sorted(names)[:3])))
                        permitted = []
                    if permitted:
                        versions.append({'knowledge_ref': version_ref, 'desc': version['desc'],
                                         'allowed_slots': permitted})
                if versions:
                    visible = {'kind': value['kind'], 'versions': versions}
                    factual_slots = [slot for slot in slots
                                     if any(slot in v['allowed_slots'] for v in versions)]
            elif kind == 'character_state':
                factual_slots = permission('character_state', ref)
                visible = {'fate': value['fate']}
            elif kind == 'setting':
                factual_slots = permission('setting', ref)
                visible = value
            state_permissions = []
            for state in current_states:
                if state.global_ref is None or _key(state.global_ref.model_dump()) != (kind, ref):
                    continue
                state_slots = permission('state_reveal', state.state_id)
                if state.modality == 'world':
                    if not set(state_slots) <= set(factual_slots):
                        raise RefusedByStructure('客观状态的披露不能越过所引用主体的真相许可')
                elif state_slots:
                    if kind == 'world_rule' and not factual_slots:
                        raise RefusedByStructure('状态引用世界规则缺少可用版本；须明确有据命题，不能猜规则版本')
                    state_permissions.append({'state_ref': state.state_id, 'modality': state.modality,
                                              'allowed_slots': state_slots})
            if not factual_slots and not state_permissions:
                restricted.append({'kind': kind, 'ref': ref})
            else:
                if visible is None:
                    raise RefusedByStructure('有表达许可但缺少可解析的共享主体正文')
                global_rows.append({'subject_ref': {'kind': kind, 'ref': ref}, 'value': visible,
                                    'allowed_slots': factual_slots,
                                    'state_permissions': state_permissions})

        # 目标可以要求暂不点破未开放主体，但不能把隐藏答案当作普通目标下发。
        restricted_keys = {_key(row) for row in restricted}
        for name in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
            goal = chapter[name]
            if any(_key(ref) in restricted_keys for ref in goal['subject_refs']):
                if goal['intent'] != 'withhold':
                    raise RefusedByStructure('目标引用未开放主体，只能明确保留未知，不能提前落地')
            for event in events:
                if event['kind'] not in ('secret_reveal', 'relation_visibility',
                                         'world_rule_reveal', 'state_reveal', 'character_state'):
                    continue
                if (event['subject_ref'] in goal['subject_refs']
                        and goal['intent'] != 'withhold'
                        and goal['anchor_scene_ref'] not in event['allowed_slots']):
                    raise RefusedByStructure('目标端点早于其必要知识披露许可')

        scene_rows = []
        for scene in chapter['scenes']:
            slot = scene['slot']
            deliveries = []
            for placement in chapter['beat_placements']:
                for delivery in placement['deliveries']:
                    if delivery['scene_ref'] != slot:
                        continue
                    stages = []
                    for ref in delivery['source_context_refs']:
                        stage = contexts[ref]
                        present = stage['present_char_ids']
                        stages.append({
                            'time_layer': stage['time_layer'], 'place': stage['place'],
                            'present': None if present is None or any(cid not in display for cid in present)
                                       else [display[cid] for cid in present],
                        })
                    deliveries.append({
                        'beat_ref': placement['beat_ref'],
                        'presentation_instruction': delivery['presentation_instruction'],
                        'stages': stages,
                    })
            scene_rows.append({
                **scene, 'deliveries': deliveries,
                'state_events': [{'state_ref': row.ref, 'endpoint': row.endpoint}
                                 for row in compiled.bindings
                                 if row.kind == 'state' and row.scene_ref == slot],
                'knowledge_event_refs': [row['event_ref'] for row in events
                                        if slot in row['allowed_slots']],
                'constraint_refs': sorted({row.ref for row in compiled.bindings
                                           if row.kind == 'constraint' and row.scene_ref == slot}),
            })
        address = []
        for cid, character in characters.items():
            if cid not in display:
                continue
            for item in character['address']:
                if item['from_chapter'] <= ordinal:
                    by = item['by']
                    address.append({'who': display[cid], 'surface': item['surface'],
                                    'by': display.get(by) if str(by).startswith('char_') else by,
                                    'context': item['context']})
        from novel.stages.stage4.measure import semantic_contract_of
        row = {
            **{key: chapter[key] for key in ('output_ordinal', 'chapter_goal', 'open_hook_goal',
                    'end_hook_goal', 'chapter_rewrite_brief')},
            'semantic_contract': semantic_contract_of(bp.model_dump(mode='json')),
            'beats': [{'beat_ref': b.beat_id, 'content': b.content,
                       'participant_char_ids': b.participant_char_ids} for b in bp.beats],
            'states': [{'state_ref': s.state_id, 'content': s.content,
                        'global_ref': s.global_ref.model_dump() if s.global_ref else None,
                        'modality': s.modality, 'holder_char_id': s.holder_char_id}
                       for s in current_states],
            'constraints': [{'constraint_ref': c.constraint_id,
                             **c.model_dump(exclude={'constraint_id', 'evidence_refs'})}
                            for c in bp.constraints],
            'global_subjects': global_rows, 'restricted_subject_refs': restricted,
            'knowledge_events': events, 'scenes': scene_rows, 'address': address,
            'characters': [{'char_id': cid, 'display_name': name, 'role': characters[cid]['role']}
                           for cid, name in sorted(display.items())],
            'narration': [_name_narration(segment.model_dump(), display, i, len(bp.narration))
                          for i, segment in enumerate(bp.narration)],
        }
        # 叫法替换不是秘密裁剪；仅使用已证实的记账标签替换规则。
        typed = CtxChapter.model_validate(rename_all(row, subs))
        result.append(typed.model_dump(mode='json', exclude_none=False))
    asm.ctx = {'chapters': result}
    return asm
