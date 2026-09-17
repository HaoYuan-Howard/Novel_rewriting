"""人物呈现按实际使用章解析；合法 Blueprint/KL 前置不能在 A/B 被扩大成全书歧义。"""
from __future__ import annotations

from copy import deepcopy
import json

from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.plot import PlotBlueprint
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.stages.stage2.shared.character_refs import blueprint_character_prompt_inventory
from novel.stages.stage2.shared.paragraphs import span_of
from novel.stages.stage2.blueprint.compile import source_digest, validate_actors, validate_evidence
from novel.stages.stage4.plot_design import build_blind_projection
from novel.stages.stage4.plot_materials import chapter_materials, chapter_materials_identity
from tests.plot_fixtures import GLOBAL, MAIN, REGISTRY, blueprint


def _validate(bps, main, registry):
    inherited = {}
    inventory = blueprint_character_prompt_inventory(registry['characters'], main)
    for raw, source in zip(bps['blueprints'], main, strict=True):
        names = inventory['character_bindings'][source['chapter_ordinal']]
        chapter = PlotBlueprint.model_validate(raw)
        validate_evidence(chapter, source['text'])
        validate_actors(chapter, source['text'], allowed_char_ids=frozenset(names),
                        character_bindings=names, inherited=inherited)
        inherited.update({row.presentation_id: row for row in chapter.actor_bindings})


def _single():
    bps, main, registry = blueprint(), deepcopy(MAIN), deepcopy(REGISTRY)
    actor = {'char_id': 'char_001', 'presentation_ref': 'ap01_01'}
    bp = bps['blueprints'][0]
    bp['actor_bindings'] = [{'presentation_id': 'ap01_01', 'char_id': 'char_001',
        'surfaces': ['甲'], 'public_state_ref': None, 'evidence_refs': ['e01_01']}]
    bp['beats'][0]['participants'][0] = actor
    # 其余位置仍为合法 null：该章只有一个可解析绑定，不能被误造为第二个人。
    registry['characters']['char_001']['aliases'].append(
        {'surface': '新客', 'identity_revealed_from': 2, 'evidence_refs': []})
    _validate(bps, main, registry)
    return bps, main, registry, actor


def _set_actor(value, actor):
    if isinstance(value, list):
        return [_set_actor(row, actor) for row in value]
    if isinstance(value, dict):
        if (value.get('char_id') == actor['char_id']
                and set(value) <= {'char_id', 'presentation_ref'}):
            return deepcopy(actor)
        return {key: _set_actor(row, actor) for key, row in value.items()}
    return value


def _source(bp, text):
    bp['source_sha256'] = source_digest(text)
    for anchor in bp['evidence_anchors']:
        anchor['start'], anchor['end'] = span_of(text, anchor['start_para'], anchor['end_para'])


def _add_later(bps, main, registry):
    shifted = json.dumps(bps['blueprints'][0], ensure_ascii=False)
    for prefix in ('b', 'st', 'e', 'c', 'ap'):
        shifted = shifted.replace(prefix + '01_', prefix + '02_')
    actor = {'char_id': 'char_001', 'presentation_ref': 'ap02_01'}
    second = _set_actor(json.loads(shifted), actor)
    second['chapter_ordinal'] = 2
    second['actor_bindings'][0]['surfaces'] = ['新客']
    text = main[0]['text'].replace('甲', '新客')
    _source(second, text)
    bps['blueprints'].append(second)
    main.append({'chapter_ordinal': 2, 'text': text})
    _validate(bps, main, registry)
    return actor


def test_later_presentation_does_not_make_earlier_valid_null_ambiguous():
    bps, main, registry, _old = _single()
    before = chapter_materials(1, bps, GLOBAL, main, [], characters=registry)
    before_identity = chapter_materials_identity(1, bps, GLOBAL, main, [], characters=registry)
    _add_later(bps, main, registry)
    after = chapter_materials(1, bps, GLOBAL, main, [], characters=registry)
    assert after == before
    assert chapter_materials_identity(1, bps, GLOBAL, main, [], characters=registry) == before_identity


def test_explicit_and_unique_null_share_one_resolved_presentation():
    bps, main, registry, old = _single()
    truth, mapping = build_blind_projection(bps, root_clean_sha1='a' * 40,
                                            characters=registry, global_g=GLOBAL, main_text=main)
    people = [row for row in truth['chapters'][0]['characters'] if row['surfaces'] == ['甲']]
    assert len(people) == 1
    assert mapping['participant_aliases'][people[0]['alias']] == old
    material = chapter_materials(1, bps, GLOBAL, main, [], characters=registry)
    people = [row for row in material.global_subjects if row.subject_ref.kind == 'character'
              and row.subject_ref.ref == 'char_001']
    assert len(people) == 1
    assert people[0].subject_ref.actor.model_dump(mode='json') == old


def presented():
    bps, registry = blueprint(), deepcopy(REGISTRY)
    bp = bps['blueprints'][0]
    text = '甲和新客收到入住邀请。\n\n乙在门口拒绝甲。\n\n新客决定留下。'
    main = [{'chapter_ordinal': 1, 'text': text}]
    from novel.stages.stage2.blueprint.compile import source_digest
    from novel.stages.stage2.shared.paragraphs import span_of
    bp['source_sha256'] = source_digest(text)
    for anchor in bp['evidence_anchors']:
        anchor['start'], anchor['end'] = span_of(text, anchor['start_para'], anchor['end_para'])
    registry['characters']['char_001']['role'] = '此前公开的队长'
    registry['characters']['char_001']['aliases'].append({
        'surface': '新客', 'identity_revealed_from': 2, 'evidence_refs': []})
    old = {'char_id': 'char_001', 'presentation_ref': 'ap01_01'}
    new = {'char_id': 'char_001', 'presentation_ref': 'ap01_02'}
    bp['actor_bindings'] = [
        {'presentation_id': ref['presentation_ref'], 'char_id': ref['char_id'],
         'surfaces': [surface], 'evidence_refs': ['e01_01'], 'public_state_ref': None}
        for ref, surface in ((old, '甲'), (new, '新客'))]
    for beat in bp['beats']:
        beat['participants'] = [old if a['char_id'] == 'char_001' else a for a in beat['participants']]
        for context in beat['source_contexts']:
            context['present'] = [old if a['char_id'] == 'char_001' else a for a in context['present']]
    bp['beats'][0]['content'] = '新客出现时，人们仍惦记甲。'
    bp['beats'][0]['participants'] = [old, new]
    bp['beats'][0]['source_contexts'][0]['present'] = [old, new]
    bp['states'][0].update(modality='claim', holder=new, content='新客声称自己要留下。')
    bp['constraints'][0].update(kind='observes', observer=new)
    bp['narration'][0].update(narrator=new, focalizer=old)
    PlotBlueprint.model_validate(bp)
    return bps, main, registry, old, new


def test_two_distinct_presentations_of_one_entity_remain_distinct():
    bps, main, registry, old, new = presented()
    _validate(bps, main, registry)
    truth, mapping = build_blind_projection(bps, root_clean_sha1='a' * 40,
                                            characters=registry, global_g=GLOBAL, main_text=main)
    first = next(row for row in truth['chapters'][0]['beats']
                 if mapping['subject_aliases'][row['alias']]['ref'] == 'b01_01')
    actors = [mapping['participant_aliases'][ref] for ref in first['participant_aliases']]
    assert len(actors) == 2
    assert {row['presentation_ref'] for row in actors} == {old['presentation_ref'], new['presentation_ref']}
    material = chapter_materials(1, bps, GLOBAL, main, [], characters=registry)
    people = [row for row in material.global_subjects if row.subject_ref.kind == 'character'
              and row.subject_ref.ref == 'char_001']
    assert {row.subject_ref.actor.presentation_ref for row in people} == {'ap01_01', 'ap01_02'}
    assert len(people) == 2
