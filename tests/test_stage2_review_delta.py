"""当前范围消费的实际材料/身份反例，替代退役 fact/hinge 快照摘要。

不把字符数、证据范围或这些确定性断言当作 Commander 语义接受。
"""
from __future__ import annotations

from dataclasses import replace
import json

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.consumption import invalidated_scopes
from novel.stages.stage2 import plot_review
from novel.stages.stage2.plot_compile import source_digest
from novel.views.plot_review import PlotReviewSnapshot
from tests.stage2_fixtures import formal_subjects, plot_blueprints
from tests.test_stage2 import global_grounding, main_text


def snapshot():
    registry, knowledge = formal_subjects(global_grounding())
    # 本组明确让母女关系从第二章进入材料，第一章才能作为真实无依赖的对照。
    knowledge.entities.relations[0].valid_from_chapter = 2
    knowledge.entities.relations[0].visibility.from_chapter = 2
    raw = plot_blueprints()
    raw['_provenance'].update(code='fixture', root_clean_sha1='0' * 40,
        inputs={}, prompts={'FIXTURE_ONLY': '0' * 10}, model='fixture')
    return PlotReviewSnapshot(None, MainText.model_validate(main_text()),
        ChapterBlueprints.model_validate(raw), knowledge, STAGE2_CHAPTER_BLUEPRINTS,
        {STAGE1_MAIN_TEXT: 'a' * 64, STAGE2_CHAPTER_BLUEPRINTS: 'b' * 64,
         STAGE2_GLOBAL_GROUNDING: 'c' * 64, STAGE2_CHARACTERS: 'd' * 64}, (), registry)


def identities(current):
    return {n: current.identity(f'chapter:{n}')[0] for n in current.ordinals}


def changed_scopes(before, after):
    old, new = identities(before), identities(after)
    return {n for n in old if old[n] != new[n]}


def pack(current, ordinal):
    scope = f'chapter:{ordinal}'
    return plot_review.build_workpack(scope, current.inputs,
        additional_selections=current.additional_selections(scope),
        extra_identity=current.knowledge_scope(scope).scope_sha256)


def test_non_name_character_change_reopens_only_real_consumers_and_supplies_identity_text():
    before, after = snapshot(), snapshot()
    after.characters.characters['char_002'].role += '，掌管家中内院日常'
    assert changed_scopes(before, after) == {2, 3}
    current = pack(after, 2)
    person = next(row for rows in current.material['dependencies'] if isinstance(rows, list)
                  for row in rows if row.get('kind') == 'character' and row['ref'] == 'char_002')
    assert person['value']['canonical'] == '沈母'
    assert person['value']['role'].endswith('掌管家中内院日常')
    assert person['value']['address'][0]['surface'] == '母亲'
    assert person['evidence'] and 'source' not in person['value']['role_evidence']


def test_relation_endpoint_changes_rebind_current_people_and_cannot_reuse_old_digest():
    before, after = snapshot(), snapshot()
    relation = after.knowledge.entities.relations[0]
    relation.subject_id, relation.object_id = relation.object_id, relation.subject_id
    assert changed_scopes(before, after) == {2, 3}
    for current in (before, after):
        people = next(row for row in current.additional_selections('chapter:2')
                      if row.projection == 'plot_characters')
        assert people.selected_ids == ('char_001', 'char_002')


def test_collection_addition_and_removal_are_seen_without_expiring_unrelated_chapter():
    before, added, removed = snapshot(), snapshot(), snapshot()
    added.knowledge.entities.relations.append(added.knowledge.entities.relations[0].model_copy(
        update={'relation_id': 'rel_02', 'predicate': '共同生活'}))
    removed.knowledge.entities.relations.clear()
    assert changed_scopes(before, added) == {2, 3}
    assert changed_scopes(before, removed) == {2, 3}


def test_unconsumed_foreign_source_edit_is_local_but_consumed_evidence_edit_is_not():
    before, after = snapshot(), snapshot()
    after.main.root[1].text += '\n\n院门外起了一阵微风。'
    after.blueprints.blueprints[1].source_sha256 = source_digest(after.main.root[1].text)
    assert changed_scopes(before, after) == {2}
    source = after.characters.characters['char_002'].role_evidence.source
    source.text, source.start = source.text[1:], source.start + 1
    after = replace(after)
    assert changed_scopes(before, after) == {2, 3}


def test_world_rule_change_or_removal_reopens_each_actual_scope():
    before, changed, removed = snapshot(), snapshot(), snapshot()
    changed.knowledge.canon.world_rules[0].versions[0].desc += '，需在日落前归家'
    removed.knowledge.canon.world_rules.clear()
    assert changed_scopes(before, changed) == {1, 2, 3}
    assert changed_scopes(before, removed) == {1, 2, 3}


def test_blueprint_content_identity_is_chapter_local_and_propagates_to_its_consumers():
    before, after = snapshot(), snapshot()
    after.blueprints.blueprints[1].beats[0].content += '，她因此急忙赶回院里。'
    assert changed_scopes(before, after) == {2}
    assert identities(before) == identities(snapshot())
    manifests = [before.identity(f'chapter:{n}')[1] for n in before.ordinals]
    assert invalidated_scopes(manifests, after.inputs, contracts={
        row.scope: row.contract for row in manifests}) == frozenset({'chapter:2'})


def test_same_character_id_with_new_evidence_changes_manifest_and_workpack_together():
    before, after = snapshot(), snapshot()
    person = after.characters.characters['char_002']
    person.role_evidence.source.end -= 1
    person.role_evidence.source.text = person.role_evidence.source.text[:-1]
    old, new = pack(before, 2), pack(after, 2)
    assert old.subject_sha256 != new.subject_sha256 and old.manifest != new.manifest
    assert old.material != new.material
    assert 'fact_atoms' not in json.dumps(new.material)


def test_character_only_referenced_by_dialogue_participant_still_invalidates_its_chapter():
    before, after = snapshot(), snapshot()
    for current in (before, after):
        current.knowledge.entities.relations.clear()
        for chapter in current.blueprints.blueprints:
            for beat in chapter.beats:
                beat.participant_char_ids = ['char_001']
                for context in beat.source_contexts:
                    context.present_char_ids = ['char_001']
        # 对白属于完整节点；没有独立 dialogue/hinge 也必须绑定参与人物。
        current.blueprints.blueprints[1].beats[0].participant_char_ids.append('char_002')
    after.characters.characters['char_002'].role += '，仍在门口等待'
    assert changed_scopes(before, after) == {2}
    people = next(row for row in after.additional_selections('chapter:2')
                  if row.projection == 'plot_characters')
    assert 'char_002' in people.selected_ids
