"""人物呈现按实际使用章解析；合法 Blueprint/KL 前置不能在 A/B 被扩大成全书歧义。"""
from __future__ import annotations

from copy import deepcopy
import json

from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.knowledge_links import KnowledgeLinkHistory, KnowledgeLinks, KnowledgeLinkSubmission
from contracts.schema.plot import PlotBlueprint
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.stages.stage2 import knowledge_links
from novel.stages.stage2.character_refs import blueprint_character_prompt_inventory
from novel.stages.stage2.paragraphs import span_of
from novel.stages.stage2.plot_compile import source_digest, validate_actors, validate_evidence
from novel.stages.stage4.plot_design import build_blind_projection
from novel.stages.stage4.plot_materials import chapter_materials, chapter_materials_identity
from tests.plot_fixtures import GLOBAL, MAIN, REGISTRY, blueprint, links


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


def _registered_later_knowledge(bps, main, audience):
    """真实重放 put 登记并由当前范围编译关联，不手写候选或跳过受众闭包。"""
    provenance = {'code': 'fixture', 'root_clean_sha1': '0' * 40, 'inputs': {},
                  'prompts': {'FIXTURE_ONLY': '0' * 10}, 'model': 'fixture'}
    formal = ChapterBlueprints.model_validate({**bps, '_provenance': {
        **provenance, 'scope_violations': [], 'context': {'architecture': 'plot-blueprint-v1',
            'source_format': 'numbered-paragraphs-v1', 'global_projection_contract': 'plot-global-scope-v2'}}})
    grounding = KnowledgeGrounding.model_validate({**GLOBAL,
        'characters_reference_sha256': 'a' * 64,
        '_provenance': {**provenance, 'evidence_locator_failures': []}})

    def scope(events=()):
        return knowledge_links.build_scope(MainText.model_validate(main), grounding, formal,
            main_sha256='1' * 64, knowledge_sha256='2' * 64, blueprint_sha256='3' * 64,
            registered_events=events)

    before = scope()
    request = KnowledgeLinkSubmission.model_validate({'scope_sha256': before.scope_sha256,
        'actor': 'fixture-commander', 'reason': '第二章新客才获知第一章的完整事件', 'decisions': [],
        'event_operations': [{'operation': 'put', 'event_id': 'ke_later', 'expected_event_sha256': None,
            'event': {'kind': 'character_knowledge', 'subject_ref': {'kind': 'beat', 'ref': 'b01_01'},
                'audience': {'kind': 'character', 'actor': audience}, 'chapter_ordinal': 2,
                'evidence_refs': [{'chapter_ordinal': 2, 'evidence_ref': 'e02_01'}]},
            'resolution': {'kind': 'at_beats', 'trigger_beat_ref': 'b02_01', 'subject_refs': [
                {'kind': 'beat', 'ref': 'b01_01'}, {'kind': 'beat', 'ref': 'b02_01'}]}}]})
    registered = knowledge_links.event_registry([KnowledgeLinkHistory(
        decision_sha256='4' * 64, accepted=True, submission=request)])
    after = scope(registered)
    applied = knowledge_links.materialize_submission(before, after, request)
    result = KnowledgeLinks.model_validate({'scope': after.model_dump(mode='json'),
        'links': [{**row.model_dump(mode='json'), 'actor': applied.actor, 'reason': applied.reason,
                   'source_decision_sha256': '4' * 64} for row in applied.decisions],
        '_provenance': links()['_provenance']})
    assert result.scope.obligations[0].audience.actor.model_dump(mode='json') == audience
    return result.model_dump(mode='json', by_alias=True)


def test_later_presentation_does_not_make_earlier_valid_null_ambiguous():
    bps, main, registry, _old = _single()
    before = chapter_materials(1, bps, GLOBAL, links(), main, [], characters=registry)
    before_identity = chapter_materials_identity(1, bps, GLOBAL, links(), main, [], characters=registry)
    _add_later(bps, main, registry)
    after = chapter_materials(1, bps, GLOBAL, links(), main, [], characters=registry)
    assert after == before
    assert chapter_materials_identity(1, bps, GLOBAL, links(), main, [], characters=registry) == before_identity


def test_future_registered_audience_uses_its_target_chapter_and_selected_proof():
    bps, main, registry, _old = _single()
    later = _add_later(bps, main, registry)
    knowledge = _registered_later_knowledge(bps, main, later)
    truth, mapping = build_blind_projection(bps, root_clean_sha1='a' * 40,
        characters=registry, knowledge_links=knowledge, global_g=GLOBAL, main_text=main)
    event = truth['chapters'][0]['knowledge'][0]
    assert event['timing'] == 'future_chapter'
    assert mapping['participant_aliases'][event['audience']['actor_alias']] == later
    material = chapter_materials(1, bps, GLOBAL, knowledge, main, [], characters=registry)
    assert 'ap02_01' in {row.presentation_id for row in material.actor_bindings}
    assert 'e02_01' in {row.evidence_ref for row in material.evidence_anchors}
    original = chapter_materials_identity(1, bps, GLOBAL, knowledge, main, [], characters=registry)
    # 第二章只消费登记/受众证据，未选择的第三段正文不能制造第一章 B 失效。
    main[1]['text'] = main[1]['text'].replace('新客决定留下。', '新客决定在此再住一晚。')
    _source(bps['blueprints'][1], main[1]['text'])
    _validate(bps, main, registry)
    changed = _registered_later_knowledge(bps, main, later)
    assert chapter_materials_identity(1, bps, GLOBAL, changed, main, [], characters=registry) == original


def test_explicit_and_unique_null_share_one_resolved_presentation():
    bps, main, registry, old = _single()
    truth, mapping = build_blind_projection(bps, root_clean_sha1='a' * 40,
                                            characters=registry, global_g=GLOBAL, main_text=main)
    people = [row for row in truth['chapters'][0]['characters'] if row['surfaces'] == ['甲']]
    assert len(people) == 1
    assert mapping['participant_aliases'][people[0]['alias']] == old
    material = chapter_materials(1, bps, GLOBAL, links(), main, [], characters=registry)
    people = [row for row in material.global_subjects if row.subject_ref.kind == 'character'
              and row.subject_ref.ref == 'char_001']
    assert len(people) == 1
    assert people[0].subject_ref.actor.model_dump(mode='json') == old


def test_two_distinct_presentations_of_one_entity_remain_distinct():
    from tests.test_writer_actor_contract import presented
    bps, main, registry, old, new = presented()
    _validate(bps, main, registry)
    truth, mapping = build_blind_projection(bps, root_clean_sha1='a' * 40,
                                            characters=registry, global_g=GLOBAL, main_text=main)
    first = next(row for row in truth['chapters'][0]['beats']
                 if mapping['subject_aliases'][row['alias']]['ref'] == 'b01_01')
    actors = [mapping['participant_aliases'][ref] for ref in first['participant_aliases']]
    assert len(actors) == 2
    assert {row['presentation_ref'] for row in actors} == {old['presentation_ref'], new['presentation_ref']}
    material = chapter_materials(1, bps, GLOBAL, links(), main, [], characters=registry)
    people = [row for row in material.global_subjects if row.subject_ref.kind == 'character'
              and row.subject_ref.ref == 'char_001']
    assert {row.subject_ref.actor.presentation_ref for row in people} == {'ap01_01', 'ap01_02'}
    assert len(people) == 2
