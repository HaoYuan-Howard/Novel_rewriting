"""story-v3 最小事实反例：实体/揭名、误信、初知、多目标、回忆与线程分层。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from tests.story_facilities import (
    _source_event, _materials, _chapter_wire, _inputs, _book_wire,
)

import pytest
from pydantic import ValidationError

from contracts.schema.source_story import (
    SourceStory, STORY_VERSION, source_revision_owner, SourceRevisionScopeError,
)
from novel.core.source_index import build_source_index, read_segments, search_literal
from novel.stages.stage2.story import (
    StoryCompileError, SourceObservationIssueRequired, associate_prompt, book_prompt, chapter_prompt, compile_associate,
    compile_book, compile_chapter, compile_names, compile_source_story, current_state, names_prompt,
    validate_source_story, book_repair_scope, apply_book_repair, book_repair_prompt,
    resolve_source_revision,
)










def _names_wire(book):
    people = {}
    for key, entity in book.story.entities.items():
        if entity.kind != 'person':
            continue
        literals = [surface.literal for surface in entity.surfaces]
        aliases = ['盛儿'] if '盛儿' in literals else []
        people[key] = {'family_group': None, 'family': None, 'given': None, 'aliases': aliases,
                       'surfaces': {literal: [{'kind': 'alias' if literal in aliases else 'text',
                           'literal': literal}] for literal in literals}}
    return {'contract_version': STORY_VERSION, 'people': people}


def _names(book, associate=None):
    return compile_names(_names_wire(book), book=book, associate=associate)


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


def test_book_unifies_multiple_local_participants_without_duplicate_formal_entities():
    index, chapters = _inputs()
    local = _chapter_wire(index, 1)
    local['referents']['身份尚未揭明的叙述者'] = deepcopy(local['referents']['叙述者'])
    local['events']['抱婴儿入屋']['participants'].append('身份尚未揭明的叙述者')
    chapters[1] = compile_chapter(local, index=index, chapter_ordinal=1)
    wire = _book_wire(index, chapters)
    book = compile_book(wire, index=index, chapters=chapters)
    event = book.story.events[book.event_handles[next(iter(chapters[1].events))]]
    assert event.participants == (book.entity_handles['叙述者'], book.entity_handles['婴儿'])
    assert len(wire['entities']['叙述者']['mention_refs']) == 3
    source = compile_source_story(index=index, chapters=chapters, book=book, names=_names(book))
    assert source.events[book.event_handles[next(iter(chapters[1].events))]] == event


def test_book_knowledge_order_depends_on_reading_position_and_supersedes_not_object_order():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    initial = compile_book(wire, index=index, chapters=chapters)
    wire['knowledge_updates'] = dict(reversed(tuple(wire['knowledge_updates'].items())))
    reordered = compile_book(wire, index=index, chapters=chapters)
    assert reordered.story.knowledge_updates == initial.story.knowledge_updates
    assert [row.mode for row in reordered.story.knowledge_updates] == ['learns', 'believes', 'rejects']
    prompt = book_prompt(index, chapters)
    assert 'knowledge_updates 字典顺序无语义' in prompt
    assert 'conflicts.status=source_ambiguous' in prompt
    assert 'source_unknown/source_conflict 放 conflicts' not in prompt


def test_book_knowledge_same_event_correction_uses_dependency_before_semantic_key():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    before = wire['knowledge_updates']['误信受邀']
    correction = wire['knowledge_updates']['纠正受邀误信']
    correction['at'] = before['at']
    wire['knowledge_updates'] = {'A_纠正': {**correction, 'supersedes': ['Z_误信']},
        'Z_误信': before}
    book = compile_book(wire, index=index, chapters=chapters)
    assert [row.mode for row in book.story.knowledge_updates] == ['believes', 'rejects']


@pytest.mark.parametrize('case', ['future', 'other_holder', 'cycle', 'self', 'unknown'])
def test_book_knowledge_order_still_rejects_invalid_correction_graph(case):
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    before = wire['knowledge_updates']['误信受邀']
    correction = wire['knowledge_updates']['纠正受邀误信']
    expected = 'knowledge_supersedes_scope'
    if case == 'future':
        before['supersedes'] = ['纠正受邀误信']
        correction['supersedes'] = []
    elif case == 'other_holder':
        correction['holder'] = '来客'
    elif case == 'cycle':
        correction['at'] = before['at']
        before['supersedes'] = ['纠正受邀误信']
        expected = 'knowledge_supersedes_cycle'
    elif case == 'self':
        before['supersedes'] = ['误信受邀']
        expected = 'knowledge_supersedes_cycle'
    else:
        correction['supersedes'] = ['不存在的认识']
        expected = 'unknown_reference'
    with pytest.raises(StoryCompileError) as caught:
        compile_book(wire, index=index, chapters=chapters)
    assert all(issue.code == expected for issue in caught.value.issues)
    assert all(issue.path[0] == 'knowledge_updates' and len(issue.path) >= 2
               for issue in caught.value.issues)


def test_book_knowledge_reports_all_independent_scope_errors_before_paid_repair():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    wire['knowledge_updates']['开场已知托付']['supersedes'] = ['误信受邀']
    wire['knowledge_updates']['纠正受邀误信']['holder'] = '来客'
    baseline = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        compile_book(wire, index=index, chapters=chapters)
    assert len(caught.value.issues) == 2
    assert {issue.code for issue in caught.value.issues} == {'knowledge_supersedes_scope'}
    assert {issue.path for issue in caught.value.issues} == {
        ('knowledge_updates', '开场已知托付'), ('knowledge_updates', '纠正受邀误信')}
    assert wire == baseline
    wire['knowledge_updates']['开场已知托付']['supersedes'] = []
    wire['knowledge_updates']['纠正受邀误信']['holder'] = '叙述者'
    assert len(compile_book(wire, index=index, chapters=chapters).story.knowledge_updates) == 3


def test_book_knowledge_reports_every_independent_cycle_without_authorizing_downstream_items():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    update = {**wire['knowledge_updates']['误信受邀'], 'at': 'story_start'}
    dependencies = {'环甲一': ['环甲二'], '环甲二': ['环甲一'],
        '环乙一': ['环乙二'], '环乙二': ['环乙一'], '自环': ['自环'], '循环的下游': ['环甲一']}
    wire['knowledge_updates'] = {key: {**update, 'supersedes': required}
        for key, required in dependencies.items()}
    baseline = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        compile_book(wire, index=index, chapters=chapters)
    assert len(caught.value.issues) == 5
    assert {issue.code for issue in caught.value.issues} == {'knowledge_supersedes_cycle'}
    assert {issue.path for issue in caught.value.issues} == {
        ('knowledge_updates', key) for key in dependencies if key != '循环的下游'}
    assert wire == baseline
    # 同一轮移除五个循环条目的错误依赖后，下游的合法显式引用可以保留。
    for key in dependencies:
        if key != '循环的下游':
            wire['knowledge_updates'][key]['supersedes'] = []
    result = compile_book(wire, index=index, chapters=chapters)
    assert len(result.story.knowledge_updates) == 6


def test_book_reports_all_invalid_and_duplicate_surfaces_in_one_narrow_repair():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    for entity in wire['entities'].values():
        entity['surfaces'][0]['literal'] += '、另一个称呼'
    duplicate = deepcopy(wire['entities']['婴儿']['surfaces'][1])
    wire['entities']['婴儿']['surfaces'].append(duplicate)
    with pytest.raises(StoryCompileError) as caught:
        compile_book(wire, index=index, chapters=chapters)
    assert len(caught.value.issues) == 4
    assert {issue.code for issue in caught.value.issues} == {'surface_not_in_evidence', 'duplicate_surface'}
    assert {issue.path for issue in caught.value.issues} == {
        ('entities', key, 'surfaces') for key in wire['entities']}
    assert all('称谓' in issue.message for issue in caught.value.issues)
    # 修复权限恰好覆盖称谓数组，可一次拆分拼接字面并合并重复证据，其他事实不动。
    repaired = deepcopy(wire)
    valid = _book_wire(index, chapters)
    for key in repaired['entities']:
        repaired['entities'][key]['surfaces'] = valid['entities'][key]['surfaces']
    assert compile_book(repaired, index=index, chapters=chapters).story.entities


def test_book_reports_reference_closure_errors_across_all_domains_in_one_repair():
    index, chapters = _inputs()
    valid = _book_wire(index, chapters)
    wire = deepcopy(valid)
    edits = [
        (('entities', '叙述者', 'mention_refs'), ['不存在的局部指称']),
        (('entities', '婴儿', 'kind'), 'object'),
        (('events', _source_event(1, 1), 'temporal', 'anchor_event'), '不存在的事件'),
        (('propositions', '托付孩子', 'entity_refs'), ['不存在的实体']),
        (('knowledge_updates', '误信受邀', 'holder'), '不存在的实体'),
        (('knowledge_updates', '误信受邀', 'proposition_ref'), '不存在的命题'),
        (('knowledge_updates', '误信受邀', 'at'), '不存在的事件'),
        (('knowledge_updates', '误信受邀', 'supersedes'), ['不存在的认识']),
        (('exposures', 0, 'event_ref'), '不存在的事件'),
        (('exposures', 0, 'proposition_ref'), '不存在的命题'),
        (('state_slots', '查身世', 'subjects'), ['不存在的实体']),
        (('state_updates', '决定查身世', 'slot_ref'), '不存在的状态槽'),
        (('state_updates', '决定查身世', 'at'), '不存在的事件'),
        (('state_updates', '决定查身世', 'proposition_ref'), '不存在的命题'),
        (('causal_links', 0, 'from_event'), '不存在的事件'),
        (('causal_links', 0, 'to_event'), '不存在的事件'),
        (('threads', '来客之由', 'updates', 0, 'event_ref'), '不存在的事件'),
        (('expression', 'focalizers'), ['不存在的实体']),
    ]
    for path, value in edits:
        parent = wire
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
    ref = list(index.chapter(1))[0]
    wire['unresolved'] = [{'key': '遗漏的关联', 'kind': 'entity_identity', 'left': '不存在的实体',
        'candidates': ['另一个不存在的实体'], 'question': '核对候选闭集', 'evidence_refs': [ref]}]
    baseline = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        compile_book(wire, index=index, chapters=chapters)
    assert len(caught.value.issues) == len(edits) + 2
    assert {issue.code for issue in caught.value.issues} == {'unknown_mention', 'mention_kind', 'unknown_reference'}
    expected = {path for path, _value in edits} - {('entities', '婴儿', 'kind')}
    expected.update({('entities', '婴儿'), ('unresolved', 0, 'left'), ('unresolved', 0, 'candidates')})
    assert {issue.path for issue in caught.value.issues} == expected
    assert wire == baseline
    # 原章owner回派优先，不能用book引用诊断掩盖明确的局部事实修订。
    wire['source_issues'] = [{'chapter_ordinal': 1, 'item': _source_event(1, 1),
        'path': ['events', _source_event(1, 1), 'summary'], 'operation': 'replace',
        'kind': 'unsupported', 'message': '局部动作须先核对原文', 'evidence_refs': [ref]}]
    with pytest.raises(SourceObservationIssueRequired):
        compile_book(wire, index=index, chapters=chapters)
    # 仅逐项恢复所报字段，其他原书事实和阅读序保持原值。
    for path, _value in edits:
        parent, original = wire, valid
        for key in path[:-1]:
            parent, original = parent[key], original[key]
        if path[-1] in original:
            parent[path[-1]] = deepcopy(original[path[-1]])
        else:
            del parent[path[-1]]
    wire['source_issues'], wire['unresolved'] = [], []
    assert wire == valid
    assert compile_book(wire, index=index, chapters=chapters) == compile_book(valid, index=index, chapters=chapters)


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
                                'evidence_refs': [index.evidence_numbers[evidence]]}
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
    assert [part.model_dump(mode='json') for part in story.entities[baby].name.surface_forms['盛儿']] == [
        {'kind': 'alias', 'alias_ref': 'al01'}]
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
         'evidence_refs': [index.evidence_numbers[list(index.chapter(2))[0]]]}]
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


def _association_case(kind='entity_identity', *, duplicate_thread=False):
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    if kind == 'thread_identity':
        wire['threads']['重复来客之由'] = deepcopy(wire['threads']['来客之由'])
        if not duplicate_thread:
            wire['threads']['重复来客之由']['updates'][0]['event_ref'] = _source_event(1, 2)
        left, candidate = '来客之由', '重复来客之由'
    else:
        left, candidate = '叙述者', '来客'
    ref = list(index.chapter(1))[0]
    wire['unresolved'] = [{'key': '同一对象', 'kind': kind, 'left': left,
        'candidates': [candidate], 'question': '是否同一对象？', 'evidence_refs': [ref]}]
    return index, chapters, wire, candidate


def _same_response(book, candidate, *, kind='entity_identity'):
    handles = book.thread_handles if kind == 'thread_identity' else book.entity_handles
    return {'contract_version': STORY_VERSION, 'verdicts': [{'kind': 'identity', 'key': '同一对象',
        'verdict': 'same', 'right': handles[candidate], 'reason': '证据支持同一对象',
        'evidence_refs': list(book.unresolved[0].evidence_refs)}]}


def _associated_names(book, answer):
    people = json.loads(names_prompt(book, associate=answer).split('【人物】', 1)[1].split('\n', 1)[0])
    return compile_names({'contract_version': STORY_VERSION, 'people': {key: {
        'family_group': None, 'family': None, 'given': None, 'aliases': [],
        'surfaces': {literal: [{'kind': 'text', 'literal': literal}] for literal in row['surfaces']}}
        for key, row in people.items()}}, book=book, associate=answer)


def test_associate_rejects_distinct_against_transitive_same_closure():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    ref = list(index.chapter(1))[0]
    wire['unresolved'] = [{'key': key, 'kind': 'entity_identity', 'left': left,
        'candidates': [right], 'question': '是否同一人？', 'evidence_refs': [ref]}
        for key, left, right in [('明确不同', '叙述者', '婴儿'),
                                 ('别称一', '婴儿', '来客'), ('别称二', '叙述者', '来客')]]
    book = compile_book(wire, index=index, chapters=chapters)
    response = {'contract_version': STORY_VERSION, 'verdicts': [
        {'kind': 'identity', 'key': '明确不同', 'verdict': 'distinct', 'right': None,
         'reason': '原文分别描写', 'evidence_refs': [ref]},
        *[{'kind': 'identity', 'key': key, 'verdict': 'same', 'right': book.entity_handles['来客'],
           'reason': '同一人', 'evidence_refs': [ref]} for key in ('别称一', '别称二')]]}
    with pytest.raises(StoryCompileError) as exc:
        compile_associate(response, book=book)
    assert {issue.code for issue in exc.value.issues} == {'association_identity_conflict'}
    assert {issue.path for issue in exc.value.issues} == {('verdicts', number) for number in range(3)}
    response['verdicts'][2].update(verdict='source_unknown', right=None)
    answer = compile_associate(response, book=book)
    source = compile_source_story(index=index, chapters=chapters, book=book,
        names=_associated_names(book, answer), associate=answer)
    assert len(source.entities) == 2
    assert source.conflicts[-1].status == 'source_ambiguous'


def test_associate_entity_merge_stably_deduplicates_state_slot_subjects():
    index, chapters, wire, candidate = _association_case()
    wire['state_slots']['同一人两个身份'] = {
        'subjects': ['叙述者', '来客'], 'aspect': 'identity', 'focus': '两个称谓指向的人'}
    book = compile_book(wire, index=index, chapters=chapters)
    before = book.model_dump(mode='json')
    answer = compile_associate(_same_response(book, candidate), book=book)
    source = compile_source_story(index=index, chapters=chapters, book=book,
        names=_associated_names(book, answer), associate=answer)
    merged = source.state_slots[book.slot_handles['同一人两个身份']]
    assert merged.subjects == (min(book.entity_handles['叙述者'], book.entity_handles['来客']),)
    assert book.model_dump(mode='json') == before


def test_associate_identical_thread_updates_merge_evidence_without_duplicate_lifecycle():
    index, chapters, wire, candidate = _association_case('thread_identity', duplicate_thread=True)
    extra = list(index.chapter(1))[1]
    wire['threads'][candidate]['updates'][0]['evidence_refs'] = [extra]
    book = compile_book(wire, index=index, chapters=chapters)
    answer = compile_associate(_same_response(book, candidate, kind='thread_identity'), book=book)
    source = compile_source_story(index=index, chapters=chapters, book=book,
        names=_associated_names(book, answer), associate=answer)
    assert len(source.threads) == 1
    thread = next(iter(source.threads.values()))
    assert [update.op for update in thread.updates] == ['open', 'resolve']
    assert set(thread.updates[0].evidence_refs) == {extra, list(index.chapter(1))[2]}


@pytest.mark.parametrize('domain', ['thread', 'state_slot'])
def test_associate_semantic_merge_scope_stops_before_acceptance_without_authorizing_verdict_patch(domain):
    if domain == 'thread':
        index, chapters, wire, candidate = _association_case('thread_identity')
        kind = 'thread_identity'
    else:
        index, chapters, wire, candidate = _association_case()
        wire['state_slots']['来客身体健康'] = {
            **wire['state_slots']['身体健康'], 'subjects': ['来客']}
        kind = 'entity_identity'
    book = compile_book(wire, index=index, chapters=chapters)
    before = book.model_dump(mode='json')
    with pytest.raises(StoryCompileError) as exc:
        compile_associate(_same_response(book, candidate, kind=kind), book=book)
    assert {issue.code for issue in exc.value.issues} == {'association_merge_scope'}
    assert {issue.item for issue in exc.value.issues} == {'同一对象'}
    assert {issue.path for issue in exc.value.issues} == {()}
    assert '不得改为distinct绕过' in str(exc.value)
    assert book.model_dump(mode='json') == before


def _proposition_truth_conflict_case():
    index, chapters = _inputs()
    wire = _book_wire(index, chapters)
    wire['propositions']['来客受邀']['statement'] = wire['propositions']['托付孩子']['statement']
    ref = list(index.chapter(1))[0]
    wire['unresolved'] = [{'key': '真假同一命题', 'kind': 'proposition_identity', 'left': '托付孩子',
        'candidates': ['来客受邀'], 'question': '两个世界地位不同的记录是否同一命题？',
        'evidence_refs': [ref]}]
    book = compile_book(wire, index=index, chapters=chapters)
    response = {'contract_version': STORY_VERSION, 'verdicts': [{
        'kind': 'identity', 'key': '真假同一命题', 'verdict': 'same',
        'right': book.proposition_handles['来客受邀'], 'reason': '身份相同但世界地位待修订',
        'evidence_refs': [ref]}]}
    return index, chapters, book, response


def test_associate_proposition_truth_conflict_requires_scope_without_rejudging_same():
    index, chapters, book, response = _proposition_truth_conflict_case()
    before = book.model_dump(mode='json')
    with pytest.raises(StoryCompileError) as exc:
        compile_associate(response, book=book)
    assert {issue.code for issue in exc.value.issues} == {'association_merge_scope'}
    assert {issue.item for issue in exc.value.issues} == {'真假同一命题'}
    assert {issue.path for issue in exc.value.issues} == {()}
    assert 'world_status' in str(exc.value) and '不得改为distinct绕过' in str(exc.value)
    assert book.model_dump(mode='json') == before
    response['verdicts'][0].update(verdict='distinct', right=None, reason='两条独立命题保持原世界地位')
    answer = compile_associate(response, book=book)
    source = compile_source_story(index=index, chapters=chapters, book=book,
        names=_associated_names(book, answer), associate=answer)
    assert source.propositions[book.proposition_handles['托付孩子']].world_status == 'established'
    assert source.propositions[book.proposition_handles['来客受邀']].world_status == 'refuted'
    assert len(source.propositions) == len(book.story.propositions)


def test_associate_truth_conflict_does_not_purchase_verdict_patch_and_replays_same_stop_free(tmp_path):
    from novel.llm.paid_requests import ValidationStop
    from tests.story_facilities import make_pipeline

    index, _chapters, book, response = _proposition_truth_conflict_case()
    case = make_pipeline(tmp_path, scripts={'associate': [response]})
    prompt = associate_prompt(book)
    compiler = lambda value: compile_associate(value, book=book)
    with pytest.raises(ValidationStop) as exc:
        case.ask('associate', prompt, compiler, index=index)
    assert exc.value.code == 'story_candidate'
    assert exc.value.context['paths'] == ()
    assert [(row['code'], row['owner'], row['item'], row['path'])
        for row in exc.value.context['diagnostics']] == [
            ('association_merge_scope', 'associate', '真假同一命题', ())]
    assert case.calls == ['associate']
    replay = make_pipeline(tmp_path, existing=case, readonly=True)
    with pytest.raises(ValidationStop) as repeated:
        replay.ask('associate', prompt, compiler, index=index)
    assert repeated.value.context['diagnostics'] == exc.value.context['diagnostics']
    assert replay.calls == []


def _repeated_character_name_case():
    old = _materials()
    index = build_source_index([{'chapter_ordinal': chapter, 'text': '\n'.join(
        segment.text for segment in old.chapter(chapter).values()).replace('开场前', '王王出现，王姑娘也在。开场前', 1)}
        for chapter in old.chapter_ordinals])
    chapters = {chapter: compile_chapter(_chapter_wire(index, chapter), index=index,
        chapter_ordinal=chapter) for chapter in index.chapter_ordinals}
    wire = _book_wire(index, chapters)
    wire['entities']['叙述者']['surfaces'].append({
        'literal': '王王', 'evidence_refs': [list(index.chapter(1))[0]]})
    book = compile_book(wire, index=index, chapters=chapters)
    people = {key: {'family_group': None, 'family': None, 'given': None, 'aliases': [],
        'surfaces': {surface.literal: [{'kind': 'text', 'literal': surface.literal}]
                     for surface in entity.surfaces}}
        for key, entity in book.story.entities.items()}
    ref = book.entity_handles['叙述者']
    people[ref].update(family='王', given='王', surfaces={
        '我': [{'kind': 'text', 'literal': '我'}], '王王': [{'kind': 'family'}, {'kind': 'given'}]})
    return index, chapters, book, ref, {'contract_version': STORY_VERSION, 'people': people}


def test_names_repeated_family_and_given_are_valid_when_full_name_is_unambiguous():
    index, chapters, book, ref, wire = _repeated_character_name_case()
    names = compile_names(wire, book=book)
    source = compile_source_story(index=index, chapters=chapters, book=book, names=names)
    assert {literal: [part.model_dump(mode='json') for part in form]
        for literal, form in source.entities[ref].name.surface_forms.items()} == {
            '我': [{'kind': 'text', 'literal': '我'}], '王王': [{'kind': 'family'}, {'kind': 'given'}]}


def test_names_repeated_components_follow_explicit_short_title_role():
    index, chapters, book, ref, wire = _repeated_character_name_case()
    data = book.model_dump(mode='json')
    data['story']['entities'][ref]['surfaces'].append({
        'literal': '王姑娘', 'evidence_refs': [list(index.chapter(1))[0]]})
    book = type(book).model_validate(data)
    wire['people'][ref]['surfaces']['王姑娘'] = [{'kind': 'family'}, {'kind': 'text', 'literal': '姑娘'}]
    source = compile_source_story(index=index, chapters=chapters, book=book,
        names=compile_names(wire, book=book))
    assert [part.kind for part in source.entities[ref].name.surface_forms['王姑娘']] == ['family', 'text']


@pytest.mark.parametrize('literal', ['', ' '])
def test_names_rejects_explicit_empty_components_and_accepts_null_unknown(literal):
    _index, _chapters, book, ref, wire = _repeated_character_name_case()
    wire['people'][ref]['family'] = literal
    with pytest.raises(StoryCompileError) as exc:
        compile_names(wire, book=book)
    assert exc.value.issues[0].code == 'name_component'
    assert exc.value.issues[0].path == ('people', ref, 'family')
    wire['people'][ref].update(family=None, given=None,
        surfaces={surface.literal: [{'kind': 'text', 'literal': surface.literal}]
                  for surface in book.story.entities[ref].surfaces})
    unknown = compile_names(wire, book=book).people[ref]
    assert unknown.family is None and unknown.given is None


def _five_person_name_batch():
    labels = ('甲一', '乙二', '来客', '丁四', '戊五')
    index = build_source_index([{'chapter_ordinal': 1, 'text': '甲一、乙二、来客、丁四与戊五相聚，甲公子先开口。'}])
    evidence = next(iter(index.evidence))
    chapter = compile_chapter({'contract_version': STORY_VERSION, 'referents': {label: {
        'kind': 'person', 'label': label, 'surface': label,
        'evidence_refs': [index.evidence_numbers[evidence]]} for label in labels},
        'events': {'相聚': {'summary': '五人在屋里相聚', 'participants': list(labels),
            'temporal': {'frame': 'current', 'anchor_event': None}, 'scene_boundary': 'new',
            'evidence_refs': [index.evidence_numbers[evidence]], 'claims': [], 'causal_hints': [], 'thread_hints': []}},
        'context_needs': []}, index=index, chapter_ordinal=1)
    mentions = {mention.label: handle for handle, mention in chapter.referents.items()}
    surfaces = {label: [label, '甲公子'] if label == '甲一' else [label] for label in labels}
    book = compile_book({'contract_version': STORY_VERSION, 'entities': {label: {
        'kind': 'person', 'label': label, 'mention_refs': [mentions[label]], 'surfaces': [
            {'literal': literal, 'evidence_refs': [evidence]} for literal in surfaces[label]]} for label in labels},
        'events': {next(iter(chapter.events)): {'temporal': {'frame': 'current', 'anchor_event': None},
            'scene_boundary': 'new'}}, 'propositions': {}, 'knowledge_updates': {}, 'exposures': [],
        'state_slots': {}, 'state_updates': {}, 'causal_links': [], 'threads': {}, 'world_rules': {},
        'expression': {'person': 'third', 'focalizers': [], 'statement': '第三人称叙述五人相聚',
            'mechanisms': [], 'evidence_refs': [evidence]}, 'conflicts': [], 'unresolved': [], 'source_issues': []},
        index=index, chapters={1: chapter})
    people = {}
    for label in labels:
        forms = ({label: [{'kind': 'text', 'literal': label}]} if label == '来客' else {
            label: [{'kind': 'family'}, {'kind': 'given'}]})
        if label == '甲一':
            forms['甲公子'] = [{'kind': 'family'}, {'kind': 'text', 'literal': '公子'}]
        people[book.entity_handles[label]] = {'family_group': None,
            'family': None if label == '来客' else label[0], 'given': None if label == '来客' else label[1],
            'aliases': [], 'surfaces': forms}
    good = {'contract_version': STORY_VERSION, 'people': people}
    bad = deepcopy(good)
    for form in bad['people'][book.entity_handles['甲一']]['surfaces'].values():
        form.append({'kind': 'text', 'literal': '错误尾字'})
    bad['people'][book.entity_handles['乙二']]['surfaces']['乙二'][1] = {'kind': 'alias', 'literal': '未声明化名'}
    bad['people'][book.entity_handles['来客']]['surfaces']['来客'][0] = {'kind': 'family'}
    bad['people'][book.entity_handles['丁四']]['aliases'] = ['未使用化名']
    bad['people'][book.entity_handles['戊五']]['surfaces']['戊五'].append({'kind': 'text', 'literal': '错误尾字'})
    return index, book, good, bad


def test_names_collects_all_independent_cross_field_errors_for_five_people_without_mutation():
    index, book, good, bad = _five_person_name_batch()
    frozen = deepcopy(bad)
    with pytest.raises(StoryCompileError) as exc:
        compile_names(bad, book=book, index=index)
    assert Counter(issue.code for issue in exc.value.issues) == {
        'name_render': 3, 'name_alias_reference': 1, 'name_component_unused': 1,
        'name_component_reference': 1, 'name_alias_coverage': 1}
    assert {issue.path[1] for issue in exc.value.issues} == set(good['people'])
    assert all(issue.item == issue.path[1] and issue.path != ('people',) for issue in exc.value.issues)
    assert all('原字段people[' in issue.message for issue in exc.value.issues
        if issue.code in ('name_component_unused', 'name_alias_coverage'))
    assert bad == frozen
    assert set(compile_names(good, book=book, index=index).people) == set(good['people'])


def test_names_five_people_are_repaired_in_one_patch_and_replayed_free(tmp_path):
    from novel.llm.ledger import CostLedger, ledger_path
    from novel.llm.paid_requests import PaidRequestSession
    from novel.llm.story_requests import request_json
    from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
    from novel.store import Str06Repository
    from novel.work_units import initialize_workflow
    from tests.story_facilities import make_context

    index, book, good, bad = _five_person_name_batch()
    calls, controls = [], []

    def respond(request, attempt):
        calls.append(request)
        if len(calls) == 1:
            value = bad
        else:
            control, _tail = json.JSONDecoder().raw_decode(
                request.user.split('【冻结候选与窄修权限】\n', 1)[1])
            controls.append(control)
            assert len(control['diagnostics']) == 7
            assert {row['path'][1] for row in control['diagnostics']} == set(good['people'])
            assert all(path != ['people'] for path in control['allowed_paths'])
            patches = []
            for path in control['allowed_paths']:
                replacement = good
                for component in path:
                    replacement = replacement[component]
                patches.append({'path': path, 'value': replacement})
            value = {'patches': patches}
        return ChatResponse(content=json.dumps(value, ensure_ascii=False), finish_reason='stop',
            usage={'prompt_tokens': 12, 'completion_tokens': 8}, model=request.model)

    base = tmp_path / 'out'
    base.mkdir()
    initialize_workflow(base)
    repository = Str06Repository(base / 'book')
    ledger = CostLedger(path=ledger_path(base))
    repository.attach_context(make_context(repository.root, ledger=ledger, node='str06.stage2.names'))
    transport = FakeTransport(respond)
    session = PaidRequestSession(repository, ledger, transport, yes=True,
        allow_source_export=True, provider='fake', sid='book')
    request = ChatRequest(model='deepseek-flash', system='只执行当前姓名解析协议', user=names_prompt(book, index=index),
        reasoning_effort='low', thinking=True, json_mode=True, max_tokens=131072)
    compiler = lambda value: compile_names(value, book=book, index=index)
    result = request_json(session, 'names', request, compiler, index=index)
    assert result.value == good and len(calls) == 2 and len(controls) == 1
    assert [step['kind'] for step in result.trace] == ['result', 'patch']
    replay = PaidRequestSession(repository, ledger, transport, yes=False,
        allow_source_export=False, provider='fake', sid='book')
    restored = request_json(replay, 'names', request, compiler, index=index)
    assert restored.value == good and len(calls) == 2
    assert compiler(restored.value) == compiler(result.value)


@pytest.mark.parametrize('change,code', [
    ('missing_surface', 'name_surface_coverage'), ('extra_surface', 'name_surface_coverage'),
    ('unknown_alias', 'name_alias_reference'), ('unused_alias', 'name_alias_coverage'),
    ('null_component', 'name_component_reference'), ('unused_component', 'name_component_unused'),
    ('wrong_render', 'name_render'), ('old_pure_titles', 'shape'), ('old_template', 'shape'),
    ('model_alias_id', 'shape'),
])
def test_names_segment_contract_rejects_incomplete_undeclared_or_retired_forms(change, code):
    _index, _chapters, book, ref, wire = _repeated_character_name_case()
    person = wire['people'][ref]
    if change == 'missing_surface':
        del person['surfaces']['王王']
    elif change == 'extra_surface':
        person['surfaces']['王姑娘'] = [{'kind': 'family'}, {'kind': 'text', 'literal': '姑娘'}]
    elif change == 'unknown_alias':
        person['surfaces']['王王'] = [{'kind': 'alias', 'literal': '未声明'}]
    elif change == 'unused_alias':
        person['aliases'] = ['未使用化名']
    elif change == 'null_component':
        person['family'] = None
    elif change == 'unused_component':
        person['surfaces']['王王'] = [{'kind': 'family'}, {'kind': 'text', 'literal': '王'}]
    elif change == 'wrong_render':
        person['surfaces']['王王'].append({'kind': 'text', 'literal': '妃'})
    elif change == 'old_pure_titles':
        person['pure_titles'] = ['我']
    elif change == 'old_template':
        person['surfaces']['王王'] = '{family}{given}'
    else:
        person['surfaces']['王王'] = [{'kind': 'alias', 'alias_ref': 'al01'}]
    with pytest.raises(StoryCompileError) as exc:
        compile_names(wire, book=book)
    assert exc.value.issues[0].code == code


@pytest.mark.parametrize('retired', ['{family}{given}', None])
def test_formal_names_refuse_retired_string_or_null_forms(retired):
    from contracts.schema.source_story import StoryNameStructure

    _index, _chapters, book, ref, wire = _repeated_character_name_case()
    name = compile_names(wire, book=book).people[ref].model_dump(mode='json')
    name['surface_forms']['王王'] = retired
    with pytest.raises(ValidationError):
        StoryNameStructure.model_validate(name)


def test_semantic_name_segments_reach_stage4_without_replacing_title_text_or_braces():
    from novel.stages.stage4.story import compile_naming

    old = _materials()
    intro = '王芷以王王妃之名出现，青衣姑娘佩着{红玉}王妃的牌子。'
    index = build_source_index([{'chapter_ordinal': chapter, 'text': '\n'.join(
        segment.text for segment in old.chapter(chapter).values()).replace('开场前', intro + '开场前', 1)}
        for chapter in old.chapter_ordinals])
    chapters = {chapter: compile_chapter(_chapter_wire(index, chapter), index=index,
        chapter_ordinal=chapter) for chapter in index.chapter_ordinals}
    raw = _book_wire(index, chapters)
    evidence = list(index.chapter(1))[0]
    raw['entities']['叙述者']['surfaces'].extend({'literal': literal, 'evidence_refs': [evidence]}
        for literal in ('王芷', '王王妃', '青衣姑娘', '{红玉}王妃'))
    book = compile_book(raw, index=index, chapters=chapters)
    wire = _names_wire(book)
    ref = book.entity_handles['叙述者']
    wire['people'][ref].update(family='王', given='芷', aliases=['青衣'], surfaces={
        '我': [{'kind': 'text', 'literal': '我'}],
        '王芷': [{'kind': 'family'}, {'kind': 'given'}],
        '王王妃': [{'kind': 'family'}, {'kind': 'text', 'literal': '王妃'}],
        '青衣姑娘': [{'kind': 'alias', 'literal': '青衣'}, {'kind': 'text', 'literal': '姑娘'}],
        '{红玉}王妃': [{'kind': 'text', 'literal': '{红玉}王妃'}]})
    names = compile_names(wire, book=book)
    source = compile_source_story(index=index, chapters=chapters, book=book, names=names)
    result = compile_naming({'protocol_version': 'story-naming-v1', 'entries': [
        {'entity_ref': ref, 'action': 'rename', 'family': '林', 'given': '舟', 'aliases': {'al01': '白衣'}},
        {'entity_ref': book.entity_handles['婴儿'], 'action': 'rename', 'aliases': {'al01': '安儿'}},
        {'entity_ref': book.entity_handles['来客'], 'action': 'keep', 'reason': '纯称谓人物'}]}, source)
    assert result.names[ref].surfaces == {
        '我': '我', '王芷': '林舟', '王王妃': '林王妃', '青衣姑娘': '白衣姑娘', '{红玉}王妃': '{红玉}王妃'}
    assert source.entities[ref].name.aliases == {'al01': '青衣'}


def test_names_and_associate_prompts_publish_closed_dynamic_schemas():
    from jsonschema import Draft202012Validator

    _index, _chapters, book, ref, names_wire = _repeated_character_name_case()
    schema = json.loads(names_prompt(book).rsplit('【输出 schema】', 1)[1])
    validator = Draft202012Validator(schema)
    validator.validate(names_wire)
    invalid = deepcopy(names_wire)
    invalid['people'][ref]['surfaces']['不在人物闭集里的称谓'] = [{'kind': 'text', 'literal': '陌生人'}]
    assert list(validator.iter_errors(invalid))
    invalid = deepcopy(names_wire)
    del invalid['people'][ref]
    assert list(validator.iter_errors(invalid))
    index, chapters, wire, candidate = _association_case()
    book = compile_book(wire, index=index, chapters=chapters)
    schema = json.loads(associate_prompt(book).rsplit('【输出 schema】', 1)[1])
    validator = Draft202012Validator(schema)
    response = _same_response(book, candidate)
    validator.validate(response)
    for change in ('missing_right', 'unknown_candidate', 'distinct_right', 'duplicate_key'):
        invalid = deepcopy(response)
        if change == 'missing_right':
            del invalid['verdicts'][0]['right']
        elif change == 'unknown_candidate':
            invalid['verdicts'][0]['right'] = 'unknown'
        elif change == 'distinct_right':
            invalid['verdicts'][0]['verdict'] = 'distinct'
        else:
            invalid['verdicts'].append(deepcopy(invalid['verdicts'][0]))
        assert list(validator.iter_errors(invalid)), change


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
    names_wire = _names_wire(book)
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


def test_source_revision_name_parts_and_segments_need_real_names_wire():
    source, inputs = _revision_case()
    ref = inputs['book'].entity_handles['婴儿']
    for relative, raw in [(('name', 'given'), ('people', ref, 'given')),
            (('name', 'aliases', 'al01'), ('people', ref, 'aliases', 0)),
            (('name', 'surface_forms', '盛儿', 0), ('people', ref, 'surfaces', '盛儿', 0))]:
        result = resolve_source_revision(source, source_ref=ref, path=relative, operation='replace', **inputs)
        assert result.owner == 'names' and result.path == raw
    with pytest.raises(StoryCompileError) as exc:
        resolve_source_revision(source, source_ref=ref, path=('name', 'given'), operation='replace',
                                **{**inputs, 'names_wire': None})
    assert exc.value.issues[0].code == 'source_revision_scope'
    resegmented = resolve_source_revision(source, source_ref=ref,
        path=('name', 'surface_forms', '盛儿'), operation='replace', **inputs)
    assert resegmented.owner == 'names' and resegmented.path == ('people', ref, 'surfaces', '盛儿')
    with pytest.raises(SourceRevisionScopeError) as exc:
        source_revision_owner(source, ref, ('name', 'surface_forms'), 'replace')
    assert exc.value.code == 'source_revision_derived'
    appended = resolve_source_revision(source, source_ref=ref,
        path=('name', 'surface_forms', '盛儿'), operation='append', **inputs)
    assert appended.owner == 'names' and appended.path == ('people', ref, 'surfaces', '盛儿')
    with pytest.raises(SourceRevisionScopeError, match='整个既有segment') as exc:
        source_revision_owner(source, ref, ('name', 'surface_forms', '盛儿', 0, 'kind'), 'replace')
    assert exc.value.code == 'source_revision_scope'
    with pytest.raises(SourceRevisionScopeError) as exc:
        source_revision_owner(source, ref, ('name', 'surface_forms', '盛儿', 0, 'alias_ref'), 'replace')
    assert exc.value.code == 'source_revision_derived'
    narrator = inputs['book'].entity_handles['叙述者']
    text = resolve_source_revision(source, source_ref=narrator,
        path=('name', 'surface_forms', '我', 0, 'literal'), operation='replace', **inputs)
    assert text.owner == 'names' and text.path == ('people', narrator, 'surfaces', '我', 0, 'literal')
    with pytest.raises(StoryCompileError) as exc:
        resolve_source_revision(source, source_ref=ref, path=('name', 'surface_forms', '盛儿', 0),
            operation='replace', **{**inputs, 'names_wire': None})
    assert exc.value.issues[0].code == 'source_revision_scope'


@pytest.mark.parametrize('direction', ['grow', 'shrink'])
def test_names_owner_atomically_resegments_growing_and_shrinking_surfaces_and_replays_free(tmp_path, direction):
    from contracts.schema.workflow import OwnerIssueV1
    from contracts.story_paths import NAME_MAP, SOURCE_STORY
    from novel.stages.chain import ChainContext, _frozen_seed, _raise_upstream, _verified_semantic_candidate
    from novel.stages.owner_revision import OwnerRevisionRequired, read_owner_issue_file
    from novel.stages.stage4.story import TargetStoryIssue
    from tests.story_facilities import make_pipeline

    case = make_pipeline(tmp_path)
    raw_book = deepcopy(case.wires['book'])
    evidence = list(case.index.chapter(2))[-1]
    raw_book['entities']['婴儿']['surfaces'].append({'literal': '孩子盛儿', 'evidence_refs': [evidence]})
    chapters = {chapter: compile_chapter(case.wires[f'ch{chapter:02d}'], index=case.index,
        chapter_ordinal=chapter) for chapter in case.index.chapter_ordinals}
    book = compile_book(raw_book, index=case.index, chapters=chapters)
    baseline = _names_wire(book)
    baby = book.entity_handles['婴儿']
    if direction == 'grow':
        surface = '孩子盛儿'
        replacement = [{'kind': 'text', 'literal': '孩子'}, {'kind': 'alias', 'literal': '盛儿'}]
    else:
        surface = '盛儿'
        baseline['people'][baby]['surfaces']['盛儿'] = [
            {'kind': 'text', 'literal': '盛'}, {'kind': 'text', 'literal': '儿'}]
        baseline['people'][baby]['surfaces']['孩子盛儿'] = [
            {'kind': 'text', 'literal': '孩子'}, {'kind': 'alias', 'literal': '盛儿'}]
        replacement = [{'kind': 'alias', 'literal': '盛儿'}]
    before = deepcopy(baseline)
    case.wires['book'], case.wires['names'] = raw_book, baseline
    case.run(through='stage2')
    repository = case.boundary.repository
    source = repository.inspect_sealed_model(SOURCE_STORY)
    assert case.calls.count('names') == 1
    original_calls = tuple(case.calls)
    with pytest.raises(SourceRevisionScopeError, match='整个既有segment'):
        source_revision_owner(source, baby, ('name', 'surface_forms', surface, 0, 'kind'), 'replace')
    assert tuple(case.calls) == original_calls

    # 下游已明确发现角色误分配；真实dispatcher负责把正式地址映射到已购wire，
    # 既不从字符串猜部件，也不向测试手工签发atomic权限。
    problem = TargetStoryIssue('本称谓需要重新分段，保持原字面和化名声明', owner='names',
        node='str06.stage2.names', item=baby, code='source_name_roles', context={
            'source_ref': baby, 'path': ['name', 'surface_forms', surface], 'operation': 'replace'})
    context = ChainContext(repository, case.ask, case.chapters, source_identity=case.index.source_identity)
    with pytest.raises(OwnerRevisionRequired) as dispatched:
        _raise_upstream(context, 'str06.stage4.naming', problem)
    assert dispatched.value.node == 'str06.stage2.names'
    issue = read_owner_issue_file(repository, 'names')
    assert issue is not None and isinstance(issue.issues[0], OwnerIssueV1)
    path = ['people', baby, 'surfaces', surface]
    assert issue.issues[0].context['path'] == path
    case.wires['names'] = {'patches': [{'path': path, 'value': replacement}]}
    case.run(through='stage4', node_filter=[
        'str06.stage2.names', 'str06.stage2.compile', 'str06.stage4.naming'])
    saved = repository.inspect('runtime/semantic_candidates/names.json')
    expected = deepcopy(before)
    expected['people'][baby]['surfaces'][surface] = replacement
    assert saved['wire'] == expected
    assert saved['revision']['seed']['atomic_paths'] == [path]
    assert saved['trace'][-1]['paths']['atomic'] == [path]
    assert case.calls.count('names') == 2
    renamed = repository.inspect_sealed_model(NAME_MAP).names[baby].surfaces[surface]
    assert renamed == ('孩子安儿' if direction == 'grow' else '安儿')
    assert read_owner_issue_file(repository, 'names').status == 'resolved'
    source_after = repository.inspect_sealed_model(SOURCE_STORY)
    assert len(source_after.entities[baby].name.surface_forms[surface]) == len(replacement)
    assert baseline == before
    # runtime保存的是可验证能力，不能靠改冻结文件或trace扩大到另一称谓、人物或列表。
    frozen_calls = tuple(case.calls)
    narrator = book.entity_handles['叙述者']
    for extra in (['people'], ['people', baby], ['people', baby, 'aliases'],
            ['people', baby, 'surfaces', '婴儿'], ['people', narrator, 'surfaces', '我']):
        forged = deepcopy(saved['revision'])
        forged['seed']['atomic_paths'].append(extra)
        with pytest.raises(ValueError):
            _frozen_seed(context, 'names', forged)
    forged = deepcopy(saved)
    forged['trace'][-1]['paths']['atomic'] = [['people', baby, 'aliases']]
    with pytest.raises(ValueError):
        _verified_semantic_candidate(context, 'names', forged)
    assert tuple(case.calls) == frozen_calls

    resumed = make_pipeline(tmp_path, existing=case, readonly=True)
    resumed.run(through='stage4', node_filter=[
        'str06.stage2.names', 'str06.stage2.compile', 'str06.stage4.naming'])
    assert resumed.calls == []
    assert resumed.boundary.repository.inspect_sealed_model(SOURCE_STORY) == source_after
    assert resumed.boundary.repository.inspect_sealed_model(NAME_MAP).names[baby].surfaces[surface] == renamed


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


def _chapter_wire_with_all_evidence_fields():
    index = _materials()
    wire = _chapter_wire(index, 2)
    ref = index.evidence_numbers[next(iter(index.chapter(2)))]
    event = wire['events']['来客解释误会']
    event['claims'] = [{'statement': '来客说明自己走错巷子', 'world_status': 'established',
        'knowledge': [], 'reader_mode': 'confirmed', 'evidence_refs': [ref]}]
    event['thread_hints'] = [{'question_or_resolution': '来客并未受邀', 'op': 'resolve',
                              'evidence_refs': [ref]}]
    wire['context_needs'] = [{'item': '前因', 'question': '此前发生了什么？', 'source_refs': [ref]}]
    return index, wire


@pytest.mark.parametrize('path', [
    ('referents', '本章来客', 'evidence_refs'),
    ('events', '来客解释误会', 'evidence_refs'),
    ('events', '来客解释误会', 'claims', 0, 'evidence_refs'),
    ('events', '来客解释误会', 'thread_hints', 0, 'evidence_refs'),
    ('context_needs', 0, 'source_refs')])
@pytest.mark.parametrize('invalid', [True, False, 1.0, '1', 'e002_00001', 0, -1])
def test_chapter_wire_requires_strict_positive_integer_evidence_in_every_domain(path, invalid):
    index, wire = _chapter_wire_with_all_evidence_fields()
    cursor = wire
    for component in path[:-1]:
        cursor = cursor[component]
    cursor[path[-1]] = [invalid]
    before = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        compile_chapter(wire, index=index, chapter_ordinal=2)
    assert all(issue.code == 'shape' for issue in caught.value.issues)
    assert path + (0,) in {issue.path for issue in caught.value.issues}
    assert wire == before


def test_chapter_integer_wire_maps_all_evidence_to_canonical_observation_without_mutation():
    from novel.stages.stage2.story import _evidence_refs
    from contracts.schema.source_story import ChapterWire, LocalReferent, ChapterObservation
    index, wire = _chapter_wire_with_all_evidence_fields()
    before = deepcopy(wire)
    assert all(type(ref) is int for _path, refs in _evidence_refs(ChapterWire.model_validate(wire)) for ref in refs)
    observation = compile_chapter(wire, index=index, chapter_ordinal=2)
    assert all(isinstance(ref, str) and ref in index.evidence
               for _path, refs in _evidence_refs(observation) for ref in refs)
    assert ChapterObservation.model_validate(observation.model_dump(mode='json')) == observation
    assert isinstance(next(iter(observation.referents.values())), LocalReferent)
    assert wire == before


def test_source_index_integer_addresses_follow_full_reading_order_with_prelude_and_are_readonly():
    from novel.core.source_index import SourceIndex
    index = build_source_index([
        {'chapter_ordinal': 1, 'text': '我在第一章。\n我又走了一段。'},
        {'chapter_ordinal': 2, 'text': '我在第二章。'}], '引子第一段。\n引子第二段。')
    assert list(index.evidence_numbers.values()) == [1, 2, 3, 4, 5]
    assert [index.evidence_ids[number] for number in range(1, 6)] == list(index.evidence)
    assert [index.evidence_numbers[ref] for ref in index.chapter(1)] == [1, 2, 3, 4]
    second = next(iter(index.chapter(2)))
    assert index.evidence_numbers[second] == 5
    queried = read_segments(index, [second], max_chapter=2)
    assert [index.evidence_numbers[ref] for ref in queried.segments] == [5]
    reordered = SourceIndex(index.source_identity, index.content_sha256, index.chapter_ordinals,
                            dict(reversed(tuple(index.evidence.items()))))
    assert dict(reordered.evidence_numbers) == dict(index.evidence_numbers)
    with pytest.raises(TypeError):
        index.evidence_numbers[second] = 1
    with pytest.raises(TypeError):
        index.evidence_ids[5] = '另一段'


def test_chapter_integer_schema_and_materials_preserve_global_addresses_and_scope():
    from jsonschema import Draft202012Validator
    from novel.stages.stage2.story import chapter_schema
    index, wire = _chapter_wire_with_all_evidence_fields()
    prior_id = next(iter(index.chapter(1)))
    current_numbers = [index.evidence_numbers[ref] for ref in index.chapter(2)]
    canonical = read_segments(index, [prior_id], max_chapter=2).material()
    material = {**canonical,
        'query': {**canonical['query'], 'segment_ids': [index.evidence_numbers[prior_id]]}, 'segments': [
        {'ref': index.evidence_numbers[ref], **segment} for ref, segment in canonical['segments'].items()]}
    schema = chapter_schema(index, 2, materials=(material,))
    for definition in schema['$defs'].values():
        evidence = definition.get('properties', {}).get('evidence_refs')
        if evidence is not None:
            assert evidence['items']['type'] == 'integer'
            assert evidence['items']['enum'] == current_numbers
    context = schema['$defs']['ChapterContextNeedWire']['properties']['source_refs']['items']
    assert context['enum'] == [index.evidence_numbers[prior_id], *current_numbers]
    wire['context_needs'][0]['source_refs'] = [index.evidence_numbers[prior_id]]
    Draft202012Validator(schema).validate(wire)
    observation = compile_chapter(wire, index=index, chapter_ordinal=2)
    assert observation.context_needs[0].source_refs == (prior_id,)
    assert list(Draft202012Validator(chapter_schema(index, 2)).iter_errors(wire))
    wrong = deepcopy(wire)
    wrong['events']['来客解释误会']['evidence_refs'] = [index.evidence_numbers[prior_id]]
    assert list(Draft202012Validator(schema).iter_errors(wrong))
    with pytest.raises(StoryCompileError):
        compile_chapter(wrong, index=index, chapter_ordinal=2)
    prompt = chapter_prompt(index, 2, materials=(material,))
    assert f'[{current_numbers[0]}]' in prompt and prior_id not in prompt
    assert not any(ref in prompt for ref in index.evidence)
    with pytest.raises(StoryCompileError):
        chapter_schema(index, 2, materials=(canonical,))
    material['segments'][0]['ref'] = True
    with pytest.raises(StoryCompileError):
        chapter_schema(index, 2, materials=(material,))


@pytest.mark.parametrize('field', ['chapter_ordinal', 'paragraph_ordinal'])
@pytest.mark.parametrize('invalid', [True, 1.0, '1'])
def test_chapter_material_ordinals_reject_coercible_metadata_before_identity_comparison(field, invalid):
    from novel.stages.stage2.story import chapter_schema
    index = _materials()
    ref = next(iter(index.chapter(1)))
    material = {'source_identity': index.source_identity, 'index_version': index.index_version,
        'segments': [{'ref': index.evidence_numbers[ref], **index.evidence[ref].model_dump(mode='json')}]}
    assert chapter_schema(index, 2, materials=(material,))
    material['segments'][0][field] = invalid
    before = deepcopy(material)
    with pytest.raises(StoryCompileError) as caught:
        chapter_schema(index, 2, materials=(material,))
    assert caught.value.issues[0].code == 'chapter_material_source'
    assert material == before


def test_chapter_canonical_evidence_array_indices_resolve_to_exact_integer_wire_fields():
    from novel.stages.stage2.story import chapter_wire_path
    index, wire = _chapter_wire_with_all_evidence_fields()
    observation = compile_chapter(wire, index=index, chapter_ordinal=2)
    event_handle = _source_event(2, 1)
    assert chapter_wire_path(observation, ('events', event_handle, 'evidence_refs', 0), wire=wire,
        index=index) == ('events', '来客解释误会', 'evidence_refs', 0)
    for domain in ('claims', 'thread_hints'):
        assert chapter_wire_path(observation, ('events', event_handle, domain, 0, 'evidence_refs', 0),
            wire=wire, index=index) == ('events', '来客解释误会', domain, 0, 'evidence_refs', 0)
    assert chapter_wire_path(observation, ('context_needs', 0, 'source_refs', 0), wire=wire,
        index=index) == ('context_needs', 0, 'source_refs', 0)
    # 正式全书回派同样经过真实wire重编比较；数组索引没有因整数地址转换漂移。
    source, inputs = _revision_case()
    formal_event = inputs['book'].event_handles[_source_event(1, 3)]
    resolved = resolve_source_revision(source, source_ref=formal_event, path=('evidence_refs', 0),
        operation='replace', **inputs)
    assert resolved.owner == 'ch01'
    assert resolved.path == ('events', '猜测来客受邀', 'evidence_refs', 0)
    number = inputs['chapter_wires'][1]['events']['猜测来客受邀']['evidence_refs'][0]
    assert type(number) is int
    assert inputs['index'].evidence_ids[number] == source.events[formal_event].evidence_refs[0]


@pytest.mark.parametrize('invalid', [False, True, 0.0, 1.0, '0', -1])
@pytest.mark.parametrize('container', ['object', 'array'])
def test_chapter_wire_path_rejects_noncanonical_object_keys_and_array_indices(container, invalid):
    from novel.stages.stage2.story import chapter_wire_path
    index, observations = _inputs()
    wire = _chapter_wire(index, 1)
    event = _source_event(1, 1)
    valid = ('events', event, 'evidence_refs', 0)
    assert chapter_wire_path(observations[1], valid, wire=wire,
        index=index) == ('events', '抱婴儿入屋', 'evidence_refs', 0)
    path = ('events', invalid) if container == 'object' else (*valid[:-1], invalid)
    before = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        chapter_wire_path(observations[1], path, wire=wire, index=index)
    assert caught.value.issues[0].code == 'source_issue_path'
    assert wire == before


@pytest.mark.parametrize('version', ['story-v1', 'story-v2'])
def test_chapter_integer_protocol_rejects_old_story_version_without_conversion(version):
    index = _materials()
    wire = _chapter_wire(index, 1)
    wire['contract_version'] = version
    with pytest.raises(StoryCompileError) as caught:
        compile_chapter(wire, index=index, chapter_ordinal=1)
    assert ('contract_version',) in {issue.path for issue in caught.value.issues}


def test_chapter_reports_all_bad_surfaces_with_exact_fields_without_changing_input():
    index = _materials()
    wire = _chapter_wire(index, 1)
    before = deepcopy(wire)
    for referent in wire['referents'].values():
        referent['surface'] += '/另一个称呼'
    broken = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        compile_chapter(wire, index=index, chapter_ordinal=1)
    assert len(caught.value.issues) == 2 * len(wire['referents'])
    assert {(issue.code, issue.path) for issue in caught.value.issues} == {
        ('mention_not_in_evidence', ('referents', key, field))
        for key in wire['referents'] for field in ('surface', 'evidence_refs')}
    assert wire == broken
    assert len(compile_chapter(before, index=index, chapter_ordinal=1).referents) == 3


def test_chapter_collects_core_and_context_evidence_failures_with_distinct_scopes():
    original = _materials()
    index = build_source_index([
        {'chapter_ordinal': chapter, 'text': '\n'.join(
            segment.text for segment in original.chapter(chapter).values())}
        for chapter in (1, 2)] + [{'chapter_ordinal': 3, 'text': '第三章才出现的新事实。'}])
    wire = _chapter_wire(index, 2)
    prior_id = next(iter(index.chapter(1)))
    prior = index.evidence_numbers[prior_id]
    current = [index.evidence_numbers[ref] for ref in index.chapter(2)]
    future = index.evidence_numbers[next(iter(index.chapter(3)))]
    wire['context_needs'] = [
        {'item': '前文前因', 'question': '前文托付的原因是什么？', 'source_refs': [prior]},
        {'item': '本章问题', 'question': '来客为何走错巷子？', 'source_refs': [current[0]]},
        {'item': '另一本章问题', 'question': '幼时病因是什么？', 'source_refs': [current[1]]}]
    before = deepcopy(wire)
    wire['referents']['叙述者']['evidence_refs'] = [len(index.evidence) + 1]
    wire['referents']['本章孩子']['surface'] = '孩子/婴儿'
    wire['events']['来客解释误会']['evidence_refs'] = [prior]
    wire['context_needs'][1]['source_refs'] = [future]
    wire['context_needs'][2]['source_refs'] = [len(index.evidence) + 2, len(index.evidence) + 2]
    broken = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        compile_chapter(wire, index=index, chapter_ordinal=2)
    assert Counter((issue.code, issue.path) for issue in caught.value.issues) == Counter({
        ('evidence_outside_scope', ('referents', '叙述者', 'evidence_refs')): 1,
        ('evidence_outside_scope', ('events', '来客解释误会', 'evidence_refs')): 1,
        ('evidence_outside_scope', ('context_needs', 1, 'source_refs')): 1,
        ('evidence_outside_scope', ('context_needs', 2, 'source_refs')): 2,
        ('duplicate_evidence', ('context_needs', 2, 'source_refs')): 1,
        ('mention_not_in_evidence', ('referents', '本章孩子', 'surface')): 1,
        ('mention_not_in_evidence', ('referents', '本章孩子', 'evidence_refs')): 1})
    assert wire == broken
    # 前文证据只可用于 context_needs；恢复原值后同一 compiler 通过，未扩大正文事实权限。
    restored = compile_chapter(before, index=index, chapter_ordinal=2)
    assert restored.context_needs[0].source_refs == (prior_id,)
    assert len(restored.events) == len(before['events'])


def test_chapter_surface_mismatch_authorizes_both_exact_fields_without_prescribing_truncation():
    index = build_source_index([{'chapter_ordinal': 1, 'text':
        '店主林舟在柜台。\n这位店主又被称作林公子。'}])
    first_id, second_id = index.chapter(1)
    first, second = index.evidence_numbers[first_id], index.evidence_numbers[second_id]
    wire = {'contract_version': STORY_VERSION,
        'referents': {'店主': {'kind': 'person', 'label': '店主', 'surface': '林舟',
                            'evidence_refs': [second]}},
        'events': {'店主被称林公子': {'summary': '店主又被称作林公子', 'participants': ['店主'],
            'temporal': {'frame': 'current', 'anchor_event': None}, 'scene_boundary': 'unknown',
            'evidence_refs': [second], 'claims': [], 'causal_hints': [], 'thread_hints': []}},
        'context_needs': []}
    before = deepcopy(wire)
    with pytest.raises(StoryCompileError) as caught:
        compile_chapter(wire, index=index, chapter_ordinal=1)
    assert len(caught.value.issues) == 2
    assert {issue.path for issue in caught.value.issues} == {
        ('referents', '店主', 'surface'), ('referents', '店主', 'evidence_refs')}
    assert all('主体含义' in issue.message and '截词' in issue.message for issue in caught.value.issues)
    assert wire == before
    corrected_evidence = deepcopy(wire)
    corrected_evidence['referents']['店主']['evidence_refs'] = [first]
    assert next(iter(compile_chapter(corrected_evidence, index=index,
        chapter_ordinal=1).referents.values())).surface == '林舟'
    corrected_surface = deepcopy(wire)
    corrected_surface['referents']['店主']['surface'] = '林公子'
    assert next(iter(compile_chapter(corrected_surface, index=index,
        chapter_ordinal=1).referents.values())).evidence_refs == (second_id,)


def test_surface_contract_describes_one_verbatim_name_without_banning_real_punctuation():
    from contracts.schema.source_story import ChapterWire, BookWire
    chapter_schema = ChapterWire.model_json_schema()['$defs']['ChapterReferentWire']['properties']['surface']
    book_schema = BookWire.model_json_schema()['$defs']['StorySurface']['properties']['literal']
    assert '一个' in chapter_schema['description'] and '证据' in chapter_schema['description']
    assert '多个称呼分别登记' in book_schema['description']
    index = build_source_index([{'chapter_ordinal': 1, 'text': '我见到了名叫甲/乙的店主。'}])
    ref = index.evidence_numbers[next(iter(index.chapter(1)))]
    wire = {'contract_version': STORY_VERSION,
        'referents': {'店主': {'kind': 'person', 'label': '店主', 'surface': '甲/乙', 'evidence_refs': [ref]}},
        'events': {'见到店主': {'summary': '我见到店主', 'participants': ['店主'],
            'temporal': {'frame': 'current', 'anchor_event': None}, 'scene_boundary': 'unknown',
            'evidence_refs': [ref], 'claims': [], 'causal_hints': [], 'thread_hints': []}},
        'context_needs': []}
    result = compile_chapter(wire, index=index, chapter_ordinal=1)
    assert next(iter(result.referents.values())).surface == '甲/乙'
    assert '不能填“我/林舟/林公子”' in chapter_prompt(index, 1)


def test_chapter_prompt_schema_never_grants_book_temporal_anchor_permission():
    import json
    from contracts.schema.source_story import ChapterWire
    index = _materials()
    prompt = chapter_prompt(index, 1)
    schema = json.loads(prompt.split('【输出 schema】', 1)[1])
    assert schema['$defs']['StoryTemporal']['properties']['anchor_event'] == {'type': 'null'}
    assert '所属事件' in schema['$defs']['ChapterClaimWire']['properties']['evidence_refs']['description']
    assert 'temporal.anchor_event 必须为 null' in prompt
    # 只投影本节点的权限，共享正式结构仍保留 book 的时间锚字段。
    assert ChapterWire.model_json_schema()['$defs']['StoryTemporal']['properties']['anchor_event'] != {'type': 'null'}


def test_chapter_collects_independent_references_and_local_constraint_failures():
    index = _materials()
    wire = _chapter_wire(index, 1)
    event = wire['events']['抱婴儿入屋']
    refs = [index.evidence_numbers[ref] for ref in index.chapter(1)]
    event['participants'] = ['未知主体', '未知主体']
    event['temporal']['anchor_event'] = '模型自造句柄'
    event['claims'] = [{'statement': '局部声明', 'world_status': 'undetermined',
        'reader_mode': 'none', 'evidence_refs': [refs[1]],
        'knowledge': [{'holder': '未知持有者', 'mode': 'suspects', 'polarity': 'undetermined'}]}]
    with pytest.raises(StoryCompileError) as caught:
        compile_chapter(wire, index=index, chapter_ordinal=1)
    codes = Counter(issue.code for issue in caught.value.issues)
    assert codes == {'unknown_reference': 3, 'duplicate_participant': 1,
                     'local_temporal_anchor': 1, 'claim_outside_event': 2}


def _associate_material_case():
    old = _materials()
    index = build_source_index([{'chapter_ordinal': chapter, 'text': '\n'.join(
        segment.text for segment in old.chapter(chapter).values()) +
        ('\n后来另一段明确提到两个人物分别离开。' if chapter == 2 else '')}
        for chapter in old.chapter_ordinals])
    chapters = {chapter: compile_chapter(_chapter_wire(old, chapter), index=index, chapter_ordinal=chapter)
                for chapter in index.chapter_ordinals}
    wire = _book_wire(index, chapters)
    ref, extra = list(index.chapter(1))[0], list(index.chapter(2))[-1]
    wire['unresolved'] = [{'key': key, 'kind': 'entity_identity', 'left': left,
        'candidates': ['来客'], 'question': '是否同一对象？', 'evidence_refs': [ref]}
        for key, left in [('甲候选', '叙述者'), ('乙候选', '婴儿')]]
    book = compile_book(wire, index=index, chapters=chapters)
    material = read_segments(index, [extra]).material()
    material['candidate_key'] = '甲候选'
    response = {'contract_version': STORY_VERSION, 'verdicts': [{'kind': 'identity', 'key': key,
        'verdict': 'distinct', 'right': None, 'reason': '证据明确不同', 'evidence_refs': [evidence]}
        for key, evidence in [('甲候选', extra), ('乙候选', ref)]]}
    return index, chapters, book, material, response, extra


def test_associate_delivered_candidate_evidence_becomes_formal_and_reaches_source_story():
    from contracts.schema.source_story import AssociateObservation
    from jsonschema import Draft202012Validator
    index, chapters, book, material, response, extra = _associate_material_case()
    assert extra not in book.story.evidence
    with pytest.raises(StoryCompileError) as caught:
        compile_associate(response, book=book, index=index)
    assert caught.value.issues[0].code == 'association_evidence'
    observation = compile_associate(response, book=book, index=index, materials=(material,))
    assert observation.supplemental_evidence[0].candidate_key == '甲候选'
    assert observation.supplemental_evidence[0].evidence[extra] == index.evidence[extra]
    restored = AssociateObservation.model_validate(observation.model_dump(mode='json'))
    assert compile_associate(restored, book=book, index=index) == observation
    people = json.loads(names_prompt(book, associate=restored, index=index).split('【人物】', 1)[1].split('\n', 1)[0])
    names = compile_names({'contract_version': STORY_VERSION, 'people': {key: {
        'family_group': None, 'family': None, 'given': None, 'aliases': [],
        'surfaces': {literal: [{'kind': 'text', 'literal': literal}] for literal in row['surfaces']}}
        for key, row in people.items()}}, book=book, associate=restored, index=index)
    story = compile_source_story(index=index, chapters=chapters, book=book, names=names, associate=restored)
    assert story.evidence[extra] == index.evidence[extra] and extra not in book.story.evidence
    schema = json.loads(associate_prompt(book, index=index, materials=(material,)).rsplit('【输出 schema】', 1)[1])
    Draft202012Validator(schema).validate(response)
    crossed = deepcopy(response)
    crossed['verdicts'][1]['evidence_refs'] = [extra]
    assert list(Draft202012Validator(schema).iter_errors(crossed))
    with pytest.raises(StoryCompileError) as caught:
        compile_associate(crossed, book=book, index=index, materials=(material,))
    assert caught.value.issues[0].code == 'association_evidence'


@pytest.mark.parametrize('tamper', ('source', 'book', 'candidate', 'evidence', 'missing_index', 'wire_metadata'))
def test_associate_formal_evidence_rejects_wrong_binding_or_model_supplied_metadata(tamper):
    from contracts.schema.source_story import AssociateObservation
    index, _chapters, book, material, response, extra = _associate_material_case()
    observation = compile_associate(response, book=book, index=index, materials=(material,))
    data = observation.model_dump(mode='json')
    if tamper == 'source':
        data['source_identity'] = '另一本书'
    elif tamper == 'book':
        data['book_sha256'] = '0' * 64
    elif tamper == 'candidate':
        data['supplemental_evidence'][0]['candidate_key'] = '乙候选'
    elif tamper == 'evidence':
        data['supplemental_evidence'][0]['evidence'][extra]['text'] += '伪造'
    if tamper == 'wire_metadata':
        with pytest.raises(StoryCompileError) as caught:
            compile_associate(data, book=book, index=index)
        assert all(issue.code == 'shape' for issue in caught.value.issues)
    else:
        restored = AssociateObservation.model_validate(data)
        with pytest.raises(StoryCompileError) as caught:
            compile_associate(restored, book=book, index=None if tamper == 'missing_index' else index)
        assert caught.value.issues[0].code in ('association_input_changed', 'association_material_source')
