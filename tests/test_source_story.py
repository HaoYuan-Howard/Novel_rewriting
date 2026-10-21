"""story-v1 最小事实反例：实体/揭名、误信、初知、多目标、回忆与线程分层。"""
from __future__ import annotations

from copy import deepcopy
from tests.story_facilities import _source_event, _materials, _chapter_wire, _inputs, _book_wire

import pytest
from pydantic import ValidationError

from contracts.schema.source_story import (
    SourceStory, STORY_VERSION, source_revision_owner, SourceRevisionScopeError,
)
from novel.core.source_index import build_source_index, read_segments, search_literal
from novel.stages.stage2.story import (
    StoryCompileError, SourceObservationIssueRequired, associate_prompt, book_prompt, chapter_prompt, compile_associate,
    compile_book, compile_chapter, compile_names, compile_source_story, current_state,
    validate_source_story, book_repair_scope, apply_book_repair, book_repair_prompt,
    resolve_source_revision,
)










def _names(book, associate=None):
    people = {}
    for key, entity in book.story.entities.items():
        literals = [surface.literal for surface in entity.surfaces]
        aliases = ['盛儿'] if '盛儿' in literals else []
        people[key] = {'family_group': None, 'family': None, 'given': None, 'aliases': aliases,
                       'pure_titles': [literal for literal in literals if literal not in aliases]}
    return compile_names({'contract_version': STORY_VERSION, 'people': people}, book=book, associate=associate)


def _compiled():
    index, chapters = _inputs()
    book = compile_book(_book_wire(index, chapters), index=index, chapters=chapters)
    return index, chapters, book, compile_source_story(index=index, chapters=chapters,
                                                      book=book, names=_names(book))


def _repair_case():
    index, after = _inputs()
    bad = _chapter_wire(index, 1)
    bad['events']['猜测来客受邀']['summary'] = '姐姐请来的客人走进屋里'
    before = {**after, 1: compile_chapter(bad, index=index, chapter_ordinal=1)}
    wire = _book_wire(index, before)
    evidence = list(index.chapter(1))[2]
    wire['source_issues'] = [{'chapter_ordinal': 1, 'item': _source_event(1, 3),
        'path': ['events', _source_event(1, 3), 'summary'], 'kind': 'contradiction',
        'operation': 'replace',
        'message': '本章写的是猜测，不能抽成客人受邀进屋', 'evidence_refs': [evidence]}]
    repair = {'contract_version': STORY_VERSION, 'kind': 'book_repair', 'changes': [
        {'path': ['source_issues'], 'operation': 'set', 'value': [],
         'reason': '章观察已改为原文猜测', 'evidence_refs': [evidence]}]}
    return index, before, after, wire, repair


@pytest.mark.parametrize('chapter_ordinal', [True, 1.0, '1'])
def test_source_issue_rejects_coerced_chapter_scope(chapter_ordinal):
    from contracts.schema.source_story import SourceObservationIssue

    _, _, _, wire, _ = _repair_case()
    issue = {**wire['source_issues'][0], 'chapter_ordinal': chapter_ordinal}
    with pytest.raises(ValidationError, match='chapter_ordinal'):
        SourceObservationIssue.model_validate(issue)


def test_chapter_handle_identity_is_independent_of_other_keys_and_payload_edits():
    index = _materials()
    original = _chapter_wire(index, 1)
    before = compile_chapter(original, index=index, chapter_ordinal=1)
    changed = deepcopy(original)
    changed['referents'] = {'误认者': {**changed['referents']['叙述者'], 'label': '误认者'},
                            **changed['referents']}
    changed['events'] = {'额外观察': deepcopy(changed['events']['抱婴儿入屋']), **changed['events']}
    changed['events']['猜测来客受邀']['summary'] += '，明确保留猜测身份'
    after = compile_chapter(changed, index=index, chapter_ordinal=1)
    assert set(before.referents) < set(after.referents)
    assert set(before.events) < set(after.events)
    for handle, event in before.events.items():
        assert handle in after.events
        assert after.events[handle].evidence_refs == event.evidence_refs
    del changed['referents']['误认者']
    del changed['events']['额外观察']
    removed = compile_chapter(changed, index=index, chapter_ordinal=1)
    assert tuple(removed.events) == tuple(before.events)
    assert tuple(removed.referents) == tuple(before.referents)


def test_book_state_update_handles_do_not_shift_after_removing_an_earlier_item():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    original = deepcopy(wire['state_updates'])
    wire['state_updates'] = {'临时观察': deepcopy(original['决定查身世']), **original}
    before = compile_book(wire, index=index, chapters=chapters)
    del wire['state_updates']['临时观察']
    after = compile_book(wire, index=index, chapters=chapters)
    before_ids = {row.update_id for row in before.story.state_updates}
    assert len(before_ids) == len(after.story.state_updates) + 1
    assert all(row.update_id in before_ids for row in after.story.state_updates)


def test_book_narrow_remove_preserves_untouched_entity_proposition_and_thread_handles():
    index, after = _inputs()
    raw = _chapter_wire(index, 1)
    evidence = list(index.chapter(1))[2]
    raw['referents']['误认者'] = {'kind': 'person', 'label': '误认者', 'surface': '我',
                                'evidence_refs': [evidence]}
    before = {**after, 1: compile_chapter(raw, index=index, chapter_ordinal=1)}
    mention = next(key for key, value in before[1].referents.items() if value.label == '误认者')
    wire = _book_wire(index, after)
    wire['entities'] = {'错误人物': {'kind': 'person', 'label': '误认者', 'mention_refs': [mention],
        'surfaces': [{'literal': '我', 'evidence_refs': [evidence]}]}, **wire['entities']}
    wire['propositions'] = {'错误命题': {'statement': '误认者是另一个人', 'entity_refs': ['错误人物'],
        'world_status': 'undetermined', 'evidence_refs': [evidence]}, **wire['propositions']}
    wire['threads'] = {'错误线程': {'question': '误认者是谁', 'updates': [
        {'event_ref': _source_event(1, 3), 'op': 'open', 'evidence_refs': [evidence]}]}, **wire['threads']}
    initial = compile_book(wire, index=index, chapters=before)
    wire['source_issues'] = [{'chapter_ordinal': 1, 'item': mention, 'path': ['referents', mention],
        'operation': 'remove', 'kind': 'unsupported', 'message': '本章没有另一名叙述者', 'evidence_refs': [evidence]}]
    scope = book_repair_scope(wire, index=index, before=before, after=after)
    changes = [{'path': [domain, key], 'operation': 'remove', 'value': None,
                'reason': '删除由同一误抽指称派生的整合条目', 'evidence_refs': [evidence]}
               for domain, key in (('entities', '错误人物'), ('propositions', '错误命题'), ('threads', '错误线程'))]
    changes.append({'path': ['source_issues'], 'operation': 'set', 'value': [],
                    'reason': '章观察已修正', 'evidence_refs': [evidence]})
    merged, expanded = apply_book_repair(wire, {'contract_version': STORY_VERSION,
        'kind': 'book_repair', 'changes': changes}, scope=scope, index=index)
    repaired = compile_book(merged, index=index, chapters=after)
    assert expanded == ()
    for attribute in ('entity_handles', 'proposition_handles', 'thread_handles',
                      'event_handles', 'slot_handles'):
        assert all(getattr(initial, attribute)[key] == handle
                   for key, handle in getattr(repaired, attribute).items())


def test_book_repair_inherits_changed_chapter_and_preserves_unaffected_integration():
    index, before, after, wire, repair = _repair_case()
    scope = book_repair_scope(wire, index=index, before=before, after=after)
    assert scope.changed_chapters == (1,)
    assert ('events', _source_event(1, 3)) in scope.allowed_paths
    assert ('propositions', '来客受邀') in scope.allowed_paths
    assert ('entities', '叙述者') not in scope.allowed_paths
    assert ('propositions', '孩子名字') not in scope.allowed_paths
    prompt = book_repair_prompt(wire, scope=scope, after=after)
    assert '"chapter_ordinal": 2' not in prompt
    merged, expanded = apply_book_repair(wire, repair, scope=scope, index=index)
    assert expanded == () and wire['source_issues']
    assert merged['entities'] == wire['entities']
    observed = compile_book(merged, index=index, chapters=after)
    event = observed.story.events[observed.event_handles[_source_event(1, 3)]]
    assert event.summary == after[1].events[_source_event(1, 3)].summary


@pytest.mark.parametrize('path,value', [
    (['expression', 'person'], 'third'),
    (['propositions'], {}),
    (['propositions', '孩子名字'], {'statement': '另一个没有证据的名字'}),
])
def test_book_repair_refuses_unrelated_or_whole_table_replacements(path, value):
    index, before, after, wire, repair = _repair_case()
    scope = book_repair_scope(wire, index=index, before=before, after=after)
    repair['changes'].append({**repair['changes'][0], 'path': path, 'value': value})
    with pytest.raises(StoryCompileError) as caught:
        apply_book_repair(wire, repair, scope=scope, index=index)
    assert caught.value.issues[0].code == 'book_repair_scope'


def test_book_repair_expansion_requires_new_explicit_dependency_and_records_it():
    index, before, after, wire, repair = _repair_case()
    scope = book_repair_scope(wire, index=index, before=before, after=after)
    repair['changes'].append({**repair['changes'][0], 'path': ['propositions', '孩子名字'],
        'value': {**wire['propositions']['孩子名字'],
                  'evidence_refs': [list(index.chapter(1))[2], list(index.chapter(2))[2]]},
        'reason': '明确记录新增跨章联系；完整compiler仍验证引用闭包'})
    _merged, expanded = apply_book_repair(wire, repair, scope=scope, index=index)
    assert expanded == (('propositions', '孩子名字'),)


def test_book_repair_requires_changed_observation_and_source_issue_rejudgment():
    index, before, after, wire, repair = _repair_case()
    with pytest.raises(StoryCompileError) as caught:
        book_repair_scope(wire, index=index, before=before, after=before)
    assert caught.value.issues[0].code == 'book_repair_no_progress'
    scope = book_repair_scope(wire, index=index, before=before, after=after)
    repair['changes'][0]['value'] = wire['source_issues']
    with pytest.raises(StoryCompileError) as caught:
        apply_book_repair(wire, repair, scope=scope, index=index)
    assert caught.value.issues[0].code == 'book_repair_no_progress'


def test_published_book_can_reaffirm_empty_source_issues_after_real_chapter_owner_repair():
    index, before, after, wire, repair = _repair_case()
    wire['source_issues'] = []
    published = compile_book(wire, index=index, chapters=before)
    scope = book_repair_scope(wire, index=index, before=before, after=after)
    merged, expanded = apply_book_repair(wire, repair, scope=scope, index=index)
    assert merged == wire and not expanded
    repaired = compile_book(merged, index=index, chapters=after)
    event = published.event_handles[_source_event(1, 3)]
    assert repaired.story.events[event].summary != published.story.events[event].summary
    assert repaired.story.events[event].summary == after[1].events[_source_event(1, 3)].summary


def test_unnamed_baby_exists_before_late_name_disclosure():
    index, _chapters, book, story = _compiled()
    baby = book.entity_handles['婴儿']
    first_event = next(iter(story.events.values()))
    assert baby in first_event.participants
    name_prop = book.proposition_handles['孩子名字']
    disclosures = [exposure for exposure in story.exposures if exposure.proposition_ref == name_prop]
    assert len(disclosures) == 1
    assert story.events[disclosures[0].event_ref].chapter_ordinal == 2
    assert story.entities[baby].name.surface_forms['盛儿'] == '{alias:al01}'
    assert validate_source_story(story, index=index) == ()


def test_initial_knowledge_and_correction_preserve_orthogonal_world_status():
    _index, _chapters, book, story = _compiled()
    initial, mistaken, correction = story.knowledge_updates
    assert initial.at == 'story_start'
    assert initial.proposition_ref == book.proposition_handles['托付孩子']
    assert story.propositions[mistaken.proposition_ref].world_status == 'refuted'
    assert mistaken.mode == 'believes' and correction.mode == 'rejects'
    assert correction.supersedes == (mistaken.update_id,)
    assert story.exposures[0].mode == 'suggested'


def test_specific_slots_keep_multiple_goals_and_recollection_does_not_overwrite_present():
    _index, _chapters, book, story = _compiled()
    state = current_state(story)
    assert state[book.slot_handles['查身世']] == '决定查身世'
    assert state[book.slot_handles['去赶集']] == '决定明日赶集'
    assert state[book.slot_handles['身体健康']] == '已经康复'
    assert any(update.value == '幼时病重' and update.effective_time == 'past'
               for update in story.state_updates)


def test_local_isolated_close_is_observation_but_integrated_isolated_close_is_rejected():
    index, chapters = _inputs()
    wire = _chapter_wire(index, 2)
    wire['events']['来客解释误会']['thread_hints'] = [
        {'question_or_resolution': '来客并未受邀', 'op': 'resolve',
         'evidence_refs': [list(index.chapter(2))[0]]}]
    observed = compile_chapter(wire, index=index, chapter_ordinal=2)
    assert observed.events[_source_event(2, 1)].thread_hints[0].op == 'resolve'
    integrated = _book_wire(index, chapters)
    integrated['threads']['来客之由']['updates'].pop(0)
    with pytest.raises(StoryCompileError):
        compile_book(integrated, index=index, chapters=chapters)


def test_compound_proposition_is_not_silently_split_or_rewritten():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    statement = '姐姐从未邀请来客，来客只是走错巷子'
    wire['propositions']['完整因由'] = {'statement': statement, 'entity_refs': ['来客'],
                                       'world_status': 'established',
                                       'evidence_refs': [list(index.chapter(2))[0]]}
    book = compile_book(wire, index=index, chapters=chapters)
    assert len(book.story.propositions) == 4
    assert book.story.propositions[book.proposition_handles['完整因由']].statement == statement


def test_unknown_and_unresolved_are_distinct_and_no_candidates_means_zero_associate():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    book = compile_book(wire, index=index, chapters=chapters)
    assert associate_prompt(book) is None
    original_names = _names(book)
    wire['unresolved'] = [{'key': '是否同一命题', 'kind': 'proposition_identity',
                           'left': '托付孩子', 'candidates': ['孩子名字'], 'question': '两者是否相同？',
                           'evidence_refs': [list(index.chapter(1))[0]]}]
    book = compile_book(wire, index=index, chapters=chapters)
    response = {'contract_version': STORY_VERSION, 'verdicts': [{
        'kind': 'identity', 'key': '是否同一命题', 'verdict': 'unresolved', 'reason': '模型没有判清',
        'evidence_refs': [list(index.chapter(1))[0]]}]}
    answer = compile_associate(response, book=book)
    with pytest.raises(StoryCompileError) as exc:
        compile_source_story(index=index, chapters=chapters, book=book, names=original_names, associate=answer)
    assert exc.value.issues[0].code == 'association_unresolved'
    response['verdicts'][0].update(verdict='source_unknown', reason='原文未明示')
    answer = compile_associate(response, book=book)
    story = compile_source_story(index=index, chapters=chapters, book=book, names=_names(book, answer),
                                  associate=answer)
    assert story.conflicts[-1].status == 'source_ambiguous'


def test_identity_coverage_cannot_turn_unnamed_person_into_world_or_object():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    wire['entities']['婴儿']['kind'] = 'object'
    with pytest.raises(StoryCompileError) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.issues[0].code == 'mention_kind'
    assert exc.value.issues[0].path == ('entities', '婴儿')


def test_final_compile_rejects_changed_observations_and_does_not_mutate_inputs():
    index, chapters, book, story = _compiled()
    baseline = book.model_dump(mode='json')
    changed = deepcopy(chapters)
    changed[1].events[_source_event(1, 1)].summary += '修改'
    with pytest.raises(StoryCompileError) as exc:
        compile_source_story(index=index, chapters=changed, book=book, names=_names(book))
    assert exc.value.issues[0].code == 'input_changed'
    assert book.model_dump(mode='json') == baseline
    assert SourceStory.model_validate(story.model_dump(mode='json')) == story


def test_source_queries_have_deterministic_identity_and_explicit_denominator():
    index = _materials()
    first = search_literal(index, ['我'], limit=1)
    assert first.total_hits == 5 and first.status == 'truncated' and first.next_cursor == 1
    assert search_literal(index, ['我'], limit=1).manifest_sha256 == first.manifest_sha256
    rest = search_literal(index, ['我'], cursor=first.next_cursor, limit=20)
    assert rest.status == 'complete'
    empty = search_literal(index, ['原文没有的字面'])
    assert empty.status == 'empty' and empty.total_hits == 0
    with pytest.raises(ValueError):
        read_segments(index, [next(iter(index.chapter(2)))], max_chapter=1)


def test_prompt_does_not_hide_fulltext_truncation_or_frontload_book_truth():
    index, chapters = _inputs()
    chapter = chapter_prompt(index, 1)
    assert '来客解释自己走错巷子' not in chapter
    full = book_prompt(index, chapters)
    assert full.count('【全书原文，只出现一次，一段一行】') == 1
    indexed = book_prompt(index, chapters, include_source=False)
    assert '【全书原文，只出现一次，一段一行】' not in indexed
    assert '完整章观察' in indexed and 'need_materials' in indexed
    assert _source_event(2, 3) in indexed


def test_legacy_contract_version_is_rejected_without_adaptation():
    index = _materials()
    wire = _chapter_wire(index, 1)
    wire['contract_version'] = 'stage2-chapters-v10'
    with pytest.raises(StoryCompileError) as exc:
        compile_chapter(wire, index=index, chapter_ordinal=1)
    assert exc.value.issues[0].code == 'shape'


def test_book_inherits_event_instead_of_buying_a_second_mechanical_echo():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    book = compile_book(wire, index=index, chapters=chapters)
    first = next(iter(book.story.events.values()))
    assert first.summary == chapters[1].events[_source_event(1, 1)].summary
    assert first.evidence_refs == chapters[1].events[_source_event(1, 1)].evidence_refs
    wire['events'][_source_event(1, 1)]['summary'] = '擅自改成另一个动作'
    with pytest.raises(StoryCompileError) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.issues[0].path == ('events', _source_event(1, 1), 'summary')


def test_book_reports_located_source_issues_to_chapter_owner_without_modifying_it():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    wire['source_issues'] = [{
        'chapter_ordinal': 1, 'item': _source_event(1, 3), 'path': ['events', _source_event(1, 3)],
        'operation': 'replace',
        'kind': 'unsupported', 'message': '本章误把猜测当事实，需局部owner核对',
        'evidence_refs': [list(index.chapter(1))[2]],
    }]
    before = chapters[1].model_dump(mode='json')
    with pytest.raises(SourceObservationIssueRequired) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.node == 'str06.stage2.chapters'
    assert exc.value.as_issue()['owner'] == 'ch01'
    assert chapters[1].model_dump(mode='json') == before


def test_source_issue_path_maps_existing_handles_to_actual_frozen_wire_without_guessing_ids():
    from novel.stages.stage2.story import chapter_wire_path
    index, chapters = _inputs()
    wire = _chapter_wire(index, 1)
    assert chapter_wire_path(chapters[1], ('events', _source_event(1, 3), 'claims'),
                             wire=wire, index=index) == ('events', '猜测来客受邀', 'claims')
    with pytest.raises(StoryCompileError):
        chapter_wire_path(chapters[1], ('events', 2), wire=wire, index=index)
    first_handle = next(iter(chapters[1].referents))
    assert chapter_wire_path(chapters[1], ('referents', first_handle), wire=wire,
                             index=index) == ('referents', next(iter(wire['referents'])))
    changed = deepcopy(wire)
    changed['events']['抱婴儿入屋']['summary'] += '已变更'
    with pytest.raises(StoryCompileError) as exc:
        chapter_wire_path(chapters[1], ('events', _source_event(1, 3)), wire=changed, index=index)
    assert exc.value.issues[0].code == 'input_changed'


def test_source_issue_rejects_positional_event_paths_and_compiled_metadata_paths():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    issue = {'chapter_ordinal': 1, 'item': _source_event(1, 3), 'path': ['events', 2],
             'operation': 'replace',
             'kind': 'unsupported', 'message': '核对本章动作',
             'evidence_refs': [list(index.chapter(1))[2]]}
    wire['source_issues'] = [issue]
    for path in (['events', 2], ['source_identity']):
        issue['path'] = path
        with pytest.raises(StoryCompileError) as exc:
            compile_book(wire, index=index, chapters=chapters)
        assert exc.value.issues[0].code == 'source_issue_path'


@pytest.mark.parametrize('operation,path', [
    ('replace', ('events', 'event', 'summary')),
    ('remove', ('events', 'event')),
    ('remove', ('referents', 'mention')),
    ('append', ('events',)), ('append', ('referents',)),
    ('append', ('context_needs',)), ('append', ('events', 'event', 'claims')),
])
def test_source_issue_explicit_operation_authorizes_exact_existing_entry_or_container(operation, path):
    index, chapters = _inputs()
    path = [(_source_event(1, 3) if part == 'event' else next(iter(chapters[1].referents))
             if part == 'mention' else part) for part in path]
    wire = _book_wire(index, chapters)
    wire['source_issues'] = [{'chapter_ordinal': 1, 'item': '核对原文', 'path': path,
        'operation': operation, 'kind': 'unsupported', 'message': '原文判断由语义owner执行',
        'evidence_refs': [list(index.chapter(1))[2]]}]
    before = deepcopy(chapters)
    with pytest.raises(SourceObservationIssueRequired) as exc:
        compile_book(wire, index=index, chapters=chapters)
    result = exc.value.context['source_issues'][0]
    assert result['operation'] == operation and result['path'] == path
    assert chapters == before


@pytest.mark.parametrize('operation,path,code', [
    ('replace', ('events',), 'source_issue_operation'),
    ('remove', ('events',), 'source_issue_operation'),
    ('remove', ('events', 'event', 'summary'), 'source_issue_operation'),
    ('remove', ('events', 'event', 'evidence_refs', 0), 'source_issue_operation'),
    ('remove', ('events', 'does_not_exist'), 'source_issue_path'),
    ('append', ('events', 'event'), 'source_issue_operation'),
    ('append', ('events', 'event', 'temporal'), 'source_issue_operation'),
    ('append', ('events', 'event', 'summary'), 'source_issue_operation'),
])
def test_source_issue_operation_never_grants_whole_table_fixed_field_or_list_deletion(operation, path, code):
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    wire['source_issues'] = [{'chapter_ordinal': 1, 'item': '核对原文',
        'path': [_source_event(1, 3) if part == 'event' else part for part in path],
        'operation': operation, 'kind': 'unsupported', 'message': '不能从这句话猜测修改权限',
        'evidence_refs': [list(index.chapter(1))[2]]}]
    with pytest.raises(StoryCompileError) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.issues[0].code == code


def test_source_issue_requires_operation_and_strict_path_index_instead_of_inferring_from_message():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    issue = {'chapter_ordinal': 1, 'item': _source_event(1, 3), 'path': ['events', _source_event(1, 3)],
             'kind': 'unsupported', 'message': '删除这条错误事件',
             'evidence_refs': [list(index.chapter(1))[2]]}
    wire['source_issues'] = [issue]
    with pytest.raises(StoryCompileError) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.issues[0].path == ('source_issues', 0, 'operation')
    issue['operation'] = 'remove'
    for invalid in (True, 0.0):
        issue['path'] = ['events', _source_event(1, 3), 'evidence_refs', invalid]
        with pytest.raises(StoryCompileError) as exc:
            compile_book(wire, index=index, chapters=chapters)
        assert exc.value.issues[0].code == 'shape'


def _revision_case():
    index, chapters = _inputs()
    book_wire = _book_wire(index, chapters)
    book_wire['world_rules']['托付规矩'] = {'kind': 'social_norm', 'statement': '托付孩子须照料',
        'evidence_refs': [next(iter(index.chapter(1)))]}
    book_wire['conflicts'] = [{'kind': '源书含混', 'statement': '托付的期限未说明',
        'source_refs': [next(iter(index.chapter(1)))], 'status': 'source_ambiguous'}]
    book = compile_book(book_wire, index=index, chapters=chapters)
    names = _names(book)
    source = compile_source_story(index=index, chapters=chapters, book=book, names=names)
    names_wire = {'contract_version': STORY_VERSION, 'people': {key: {
        'family_group': name.family_group, 'family': name.family, 'given': name.given,
        'aliases': list(name.aliases.values()),
        'pure_titles': [literal for literal, form in name.surface_forms.items() if form is None],
    } for key, name in names.people.items()}}
    inputs = {'book': book, 'book_wire': book_wire, 'chapters': chapters,
        'chapter_wires': {ch: _chapter_wire(index, ch) for ch in chapters},
        'index': index, 'names_wire': names_wire}
    return source, inputs


@pytest.mark.parametrize('field,owner,raw_path', [
    ('summary', 'ch01', ('events', '猜测来客受邀', 'summary')),
    ('participants', 'ch01', ('events', '猜测来客受邀', 'participants')),
    ('evidence_refs', 'ch01', ('events', '猜测来客受邀', 'evidence_refs')),
    ('temporal', 'book', ('events', 'event', 'temporal')),
    ('scene_boundary', 'book', ('events', 'event', 'scene_boundary')),
])
def test_source_revision_maps_event_fields_to_unique_actual_writer(field, owner, raw_path):
    source, inputs = _revision_case()
    ref = inputs['book'].event_handles[_source_event(1, 3)]
    result = resolve_source_revision(source, source_ref=ref, path=(field,), operation='replace', **inputs)
    assert result.owner == owner
    assert result.path == tuple(_source_event(1, 3) if part == 'event' else part for part in raw_path)
    assert result.canonical_path == ('events', ref, field)
    assert source_revision_owner(source, ref, (field,), 'replace').owner == owner


def test_source_revision_global_records_with_one_chapter_evidence_stay_book_owned():
    source, inputs = _revision_case()
    book = inputs['book']
    examples = [(book.proposition_handles['托付孩子'], ('world_status',), ('propositions', '托付孩子', 'world_status')),
        (book.entity_handles['婴儿'], ('label',), ('entities', '婴儿', 'label')),
        (book.slot_handles['查身世'], ('focus',), ('state_slots', '查身世', 'focus')),
        (book.thread_handles['来客之由'], ('question',), ('threads', '来客之由', 'question')),
        (source.knowledge_updates[1].update_id, ('polarity',), ('knowledge_updates', '误信受邀', 'polarity')),
        (source.state_updates[0].update_id, ('value',), ('state_updates', '决定查身世', 'value')),
        (next(iter(source.world_rules)), ('statement',), ('world_rules', '托付规矩', 'statement'))]
    for ref, path, expected in examples:
        result = resolve_source_revision(source, source_ref=ref, path=path, operation='replace', **inputs)
        assert result.node == 'str06.stage2.book' and result.owner == 'book'
        assert result.path == expected
    event = book.event_handles[_source_event(1, 3)]
    removed = resolve_source_revision(source, source_ref=event, path=(), operation='remove', **inputs)
    assert removed.owner == 'ch01' and removed.path == ('events', '猜测来客受邀')
    prop = book.proposition_handles['来客受邀']
    removed_prop = resolve_source_revision(source, source_ref=prop, path=(), operation='remove', **inputs)
    assert removed_prop.owner == 'book' and removed_prop.path == ('propositions', '来客受邀')


def test_source_revision_name_parts_need_real_names_wire_and_never_reverse_engineer_templates():
    source, inputs = _revision_case()
    ref = inputs['book'].entity_handles['婴儿']
    for relative, raw in [(('name', 'given'), ('people', ref, 'given')),
            (('name', 'aliases', 'al01'), ('people', ref, 'aliases', 0))]:
        result = resolve_source_revision(source, source_ref=ref, path=relative, operation='replace', **inputs)
        assert result.owner == 'names' and result.path == raw
    with pytest.raises(StoryCompileError) as exc:
        resolve_source_revision(source, source_ref=ref, path=('name', 'given'), operation='replace',
                                **{**inputs, 'names_wire': None})
    assert exc.value.issues[0].code == 'source_revision_scope'
    with pytest.raises(SourceRevisionScopeError) as exc:
        source_revision_owner(source, ref, ('name', 'surface_forms', '盛儿'), 'replace')
    assert exc.value.code == 'source_revision_derived'


@pytest.mark.parametrize('path,operation,code', [
    ((), 'replace', 'source_revision_scope'),
    (('order',), 'replace', 'source_revision_derived'),
    (('summary',), 'remove', 'source_revision_scope'),
    (('summary',), 'append', 'source_revision_operation'),
    (('claims',), 'replace', 'source_revision_path'),
    (('participants', True), 'replace', 'source_revision_path'),
])
def test_source_revision_rejects_ambiguous_derived_or_noncanonical_scope(path, operation, code):
    source, inputs = _revision_case()
    ref = inputs['book'].event_handles[_source_event(1, 3)]
    with pytest.raises(SourceRevisionScopeError) as exc:
        source_revision_owner(source, ref, path, operation)
    assert exc.value.code == code


def test_source_revision_refuses_changed_raw_inputs_instead_of_guessing_handles():
    source, inputs = _revision_case()
    ref = inputs['book'].event_handles[_source_event(1, 3)]
    snapshot = source.model_dump(mode='json')
    changed = deepcopy(inputs['chapter_wires'])
    changed[1]['events']['猜测来客受邀']['summary'] += '另一个动作'
    with pytest.raises(StoryCompileError) as exc:
        resolve_source_revision(source, source_ref=ref, path=('summary',), operation='replace',
                                **{**inputs, 'chapter_wires': changed})
    assert exc.value.issues[0].code == 'input_changed'
    changed_book = deepcopy(inputs['book_wire'])
    changed_book['propositions']['来客受邀']['statement'] += '另一个命题'
    with pytest.raises(StoryCompileError) as exc:
        resolve_source_revision(source, source_ref=ref, path=('temporal',), operation='replace',
                                **{**inputs, 'book_wire': changed_book})
    assert exc.value.issues[0].code == 'input_changed'
    assert source.model_dump(mode='json') == snapshot


def test_source_revision_append_maps_the_real_container_and_remove_never_compacts_lists():
    source, inputs = _revision_case()
    ref = inputs['book'].event_handles[_source_event(1, 3)]
    result = resolve_source_revision(source, source_ref=ref, path=('participants',),
                                     operation='append', **inputs)
    assert result.path == ('events', '猜测来客受邀', 'participants')
    assert result.operation == 'append'
    with pytest.raises(SourceRevisionScopeError) as exc:
        source_revision_owner(source, ref, ('participants', 0), 'remove')
    assert exc.value.code == 'source_revision_scope'
    baby = inputs['book'].entity_handles['婴儿']
    result = resolve_source_revision(source, source_ref=baby, path=('name', 'aliases'),
                                     operation='append', **inputs)
    assert result.path == ('people', baby, 'aliases')


@pytest.mark.parametrize('selector,path,operation', [
    ('causal_links', (0, 'support'), 'replace'),
    ('exposures', (0, 'mode'), 'replace'),
    ('conflicts', (0, 'statement'), 'replace'),
    ('expression', ('person',), 'replace'), ('expression', (), 'replace'),
    ('causal_links', (), 'append'), ('exposures', (), 'append'),
    ('conflicts', (), 'append'), ('expression', ('mechanisms',), 'append'),
])
def test_source_revision_reserved_selectors_map_idless_formal_records_without_inventing_ids(selector, path, operation):
    source, inputs = _revision_case()
    result = resolve_source_revision(source, source_ref=selector, path=path, operation=operation, **inputs)
    assert result.node == 'str06.stage2.book' and result.owner == 'book'
    assert result.canonical_path == result.path == (selector, *path)


@pytest.mark.parametrize('selector,path,operation,code', [
    ('causal_links', (), 'replace', 'source_revision_scope'),
    ('causal_links', (0,), 'remove', 'source_revision_scope'),
    ('exposures', (0,), 'remove', 'source_revision_scope'),
    ('conflicts', (0,), 'remove', 'source_revision_scope'),
    ('expression', (), 'remove', 'source_revision_scope'),
    ('expression', ('person',), 'append', 'source_revision_operation'),
])
def test_source_revision_idless_selectors_do_not_grant_table_replacement_or_array_compaction(selector, path, operation, code):
    source, _inputs = _revision_case()
    with pytest.raises(SourceRevisionScopeError) as exc:
        source_revision_owner(source, selector, path, operation)
    assert exc.value.code == code


def test_source_revision_does_not_route_associate_temporal_output_to_old_book_field():
    index, chapters, wire, book, answer = _temporal_case()
    associate = compile_associate(answer, book=book)
    source = compile_source_story(index=index, chapters=chapters, book=book,
        names=_names(book, associate), associate=associate)
    ref = book.event_handles[_source_event(2, 2)]
    with pytest.raises(StoryCompileError) as exc:
        resolve_source_revision(source, source_ref=ref, path=('temporal', 'frame'), operation='replace',
            book=book, book_wire=wire, chapters=chapters,
            chapter_wires={ch: _chapter_wire(index, ch) for ch in chapters}, index=index, associate=associate)
    assert exc.value.issues[0].code == 'source_revision_scope'
    action = resolve_source_revision(source, source_ref=ref, path=('summary',), operation='replace',
        book=book, book_wire=wire, chapters=chapters,
        chapter_wires={ch: _chapter_wire(index, ch) for ch in chapters}, index=index, associate=associate)
    assert action.owner == 'ch02' and action.path == ('events', '回想幼时病史', 'summary')


def _temporal_case():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    wire['events'][_source_event(2, 2)]['temporal'] = {'frame': 'unknown', 'anchor_event': None}
    wire['unresolved'] = [{'key': '幼年病史时间锚', 'kind': 'temporal_anchor',
        'left': _source_event(2, 2), 'candidates': [_source_event(1, 1)], 'question': '病史相对入屋是何时？',
        'evidence_refs': [list(index.chapter(2))[1]]}]
    book = compile_book(wire, index=index, chapters=chapters)
    answer = {'contract_version': STORY_VERSION, 'verdicts': [{
        'kind': 'temporal_anchor', 'key': '幼年病史时间锚', 'verdict': 'resolved',
        'frame': 'past', 'anchor_event': book.event_handles[_source_event(1, 1)],
        'reason': '原文明确回忆幼年病史', 'evidence_refs': [list(index.chapter(2))[1]]}]}
    return index, chapters, wire, book, answer


def test_temporal_associate_is_a_tagged_narrow_answer_and_final_compiler_applies_only_its_target():
    index, chapters, _wire, book, response = _temporal_case()
    before = book.model_dump(mode='json')
    prompt = associate_prompt(book)
    assert 'kind=temporal_anchor' in prompt and 'kind=identity' in prompt
    answer = compile_associate(response, book=book)
    story = compile_source_story(index=index, chapters=chapters, book=book,
        names=_names(book, answer), associate=answer)
    target = book.event_handles[_source_event(2, 2)]
    assert story.events[target].temporal.frame == 'past'
    assert story.events[target].temporal.anchor_event == book.event_handles[_source_event(1, 1)]
    assert list(story.events) == list(book.story.events)
    assert book.model_dump(mode='json') == before
    assert all(story.events[key] == event for key, event in book.story.events.items() if key != target)


def test_temporal_source_unknown_keeps_ambiguity_while_model_unresolved_blocks_publication():
    index, chapters, _wire, book, response = _temporal_case()
    response['verdicts'][0].update(verdict='source_unknown', frame='unknown', anchor_event=None)
    answer = compile_associate(response, book=book)
    names = _names(book, answer)
    story = compile_source_story(index=index, chapters=chapters, book=book, names=names, associate=answer)
    assert story.events[book.event_handles[_source_event(2, 2)]].temporal.frame == 'unknown'
    assert story.conflicts[-1].kind == 'temporal_anchor' and story.conflicts[-1].status == 'source_ambiguous'
    response['verdicts'][0]['verdict'] = 'unresolved'
    answer = compile_associate(response, book=book)
    with pytest.raises(StoryCompileError) as exc:
        compile_source_story(index=index, chapters=chapters, book=book, names=names, associate=answer)
    assert exc.value.issues[0].code == 'association_unresolved'


@pytest.mark.parametrize('change,code', [('branch', 'association_branch'),
    ('candidate', 'association_candidate'), ('evidence', 'association_evidence'), ('unknown_frame', 'shape')])
def test_temporal_candidate_frame_branch_and_evidence_are_closed(change, code):
    index, _chapters, _wire, book, response = _temporal_case()
    row = response['verdicts'][0]
    if change == 'branch':
        response['verdicts'][0] = {'kind': 'identity', 'key': row['key'], 'verdict': 'same',
            'right': row['anchor_event'], 'reason': row['reason'], 'evidence_refs': row['evidence_refs']}
    elif change == 'candidate':
        row['anchor_event'] = book.event_handles[_source_event(1, 2)]
    elif change == 'evidence':
        row['evidence_refs'] = [list(index.chapter(1))[1]]
    else:
        row['frame'] = 'unknown'
    with pytest.raises(StoryCompileError) as exc:
        compile_associate(response, book=book)
    assert exc.value.issues[0].code == code


def test_book_cannot_request_time_overwrite_or_two_time_decisions_for_one_event():
    index, chapters, wire, _book, _response = _temporal_case()
    wire['events'][_source_event(2, 2)]['temporal']['frame'] = 'past'
    with pytest.raises(StoryCompileError) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.issues[0].code == 'association_temporal_target'
    wire['events'][_source_event(2, 2)]['temporal']['frame'] = 'unknown'
    wire['unresolved'].append({**wire['unresolved'][0], 'key': '重复时间问题'})
    with pytest.raises(StoryCompileError) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.issues[0].code == 'association_temporal_target'


@pytest.mark.parametrize('path', [('events', '抱婴儿入屋', 'temporal', 'frame'),
    ('events', '抱婴儿入屋', 'temporal', 'anchor_event'), ('events', '抱婴儿入屋', 'scene_boundary'),
    ('events', '抱婴儿入屋', 'claims'), ('events', '抱婴儿入屋', 'causal_hints'), ('events', '抱婴儿入屋', 'thread_hints'),
    ('context_needs',)])
def test_local_missing_semantics_cannot_default_to_unknown_or_empty(path):
    index = _materials()
    wire = _chapter_wire(index, 1)
    parent = wire
    for component in path[:-1]:
        parent = parent[component]
    del parent[path[-1]]
    with pytest.raises(StoryCompileError) as exc:
        compile_chapter(wire, index=index, chapter_ordinal=1)
    assert exc.value.issues[0].code == 'shape' and exc.value.issues[0].path == path


@pytest.mark.parametrize('path', [('knowledge_updates', '误信受邀', 'polarity'),
    ('knowledge_updates', '误信受邀', 'supersedes'), ('state_updates', '决定查身世', 'modality'),
    ('unresolved',), ('source_issues',)])
def test_global_missing_semantics_cannot_default_to_actual_affirmative_or_no_issue(path):
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    parent = wire
    for component in path[:-1]:
        parent = parent[component]
    del parent[path[-1]]
    with pytest.raises(StoryCompileError) as exc:
        compile_book(wire, index=index, chapters=chapters)
    assert exc.value.issues[0].code == 'shape' and exc.value.issues[0].path == path


@pytest.mark.parametrize('chapters,hook,identity', [
    ([{'chapter_ordinal': True, 'text': '正文'}], '', None),
    ([{'chapter_ordinal': 1, 'text': '正文'}], None, None),
    ([{'chapter_ordinal': 1, 'text': '正文'}], '', ''),
    ([{'chapter_ordinal': 1}], '', None),
])
def test_source_index_refuses_malformed_material_without_defaulting_or_incidental_exceptions(chapters, hook, identity):
    with pytest.raises(ValueError):
        build_source_index(chapters, hook, source_identity=identity)
