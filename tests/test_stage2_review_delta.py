"""跨候选内容摘要必须把交叉复核限制到可证明范围，未知范围保持全量。"""
from __future__ import annotations

import copy

from novel.stages.stage2.review_delta import (
    ChapterScope, CrossReviewScope, GlobalItemScope, compare, snapshot,
)


def _item(path='entities.characters.char_001.role', *, summary=False, wide=False):
    return GlobalItemScope(
        item_id='role:' + path + ':' + 'a' * 12,
        subject_path=path, subject_sha256='a' * 64,
        field_sha256={'role': 'a' * 64}, char_ids=['char_001'],
        evidence_chapters=[1], whole_book=wide, collection_summary=summary)


def _scope(items=None):
    return CrossReviewScope(
        review_subject_sha256='a' * 64, main_text_sha256='f' * 64,
        global_items=items or {'role:entities.characters.char_001.role': _item()},
        chapters={
            1: ChapterScope(subject_sha256='1' * 64, char_references={
                'char_001': ['chapter:1/fact:f01_001']}),
            2: ChapterScope(subject_sha256='2' * 64, char_references={
                'char_002': ['chapter:2/fact:f02_001']}),
            3: ChapterScope(subject_sha256='3' * 64, char_references={
                'char_003': ['chapter:3/beat:b03_01']}),
        })


def _changed(before):
    after = before.model_copy(deep=True, update={'review_subject_sha256': 'b' * 64})
    key = next(iter(after.global_items))
    after.global_items[key] = after.global_items[key].model_copy(update={
        'item_id': 'role:new:' + 'b' * 12, 'subject_sha256': 'b' * 64,
        'field_sha256': {'role': 'b' * 64}})
    return after


def test_non_name_field_change_reuses_other_chapters_and_pairs_changed_identity():
    before = _scope()
    delta = compare(before, _changed(before))
    assert delta.mode == 'targeted'
    assert delta.affected_chapter_ordinals == [1]
    assert delta.reusable_accepted_chapter_ordinals == [2, 3]
    assert len(delta.changed_global_items) == 1
    row = delta.changed_global_items[0]
    assert row.change == 'changed' and row.changed_fields == ['role']
    assert row.affected_references == ['chapter:1/fact:f01_001']


def test_relation_endpoint_change_includes_old_and_current_references():
    before = _scope({'relation:entities.relations.rel_001': _item('entities.relations.rel_001')})
    after = _changed(before)
    key = next(iter(after.global_items))
    after.global_items[key] = after.global_items[key].model_copy(update={
        'char_ids': ['char_002'], 'evidence_chapters': [2],
        'field_sha256': {'object_id': 'b' * 64}})
    delta = compare(before, after)
    assert delta.affected_chapter_ordinals == [1, 2]
    assert delta.reusable_accepted_chapter_ordinals == [3]
    assert delta.changed_global_items[0].affected_references == [
        'chapter:1/fact:f01_001', 'chapter:2/fact:f02_001']


def test_collection_change_inherits_only_related_detailed_facts():
    before = _scope()
    before.global_items['characters'] = _item('entities.characters', summary=True, wide=True)
    after = _changed(before)
    after.global_items['characters'] = after.global_items['characters'].model_copy(update={
        'subject_sha256': 'c' * 64, 'field_sha256': {'members': 'c' * 64},
        'char_ids': ['char_001', 'char_002']})
    delta = compare(before, after)
    assert delta.mode == 'targeted' and delta.affected_chapter_ordinals == [1]
    aggregate = next(row for row in delta.changed_global_items if row.subject_path == 'entities.characters')
    assert aggregate.affected_references == ['chapter:1/fact:f01_001']


def test_unrelated_collection_or_changed_main_text_cannot_borrow_local_scope():
    before = _scope()
    before.global_items['secrets'] = _item('canon.secrets', summary=True, wide=True)
    after = _changed(before)
    after.global_items['secrets'] = after.global_items['secrets'].model_copy(update={'subject_sha256': 'c' * 64})
    assert compare(before, after).mode == 'full'
    before.global_items['secrets'] = _item('entities.characters', summary=True, wide=True)
    after = _changed(before)
    after.global_items['secrets'] = after.global_items['secrets'].model_copy(update={'subject_sha256': 'c' * 64})
    after.main_text_sha256 = 'e' * 64
    assert compare(before, after).mode == 'full'


def test_world_rule_scope_is_full_and_removed_fact_keeps_old_references():
    before = _scope({'rule': _item('canon.world_rules.rule_001', wide=True)})
    delta = compare(before, _changed(before))
    assert delta.mode == 'full' and delta.affected_chapter_ordinals == [1, 2, 3]
    before = _scope()
    after = before.model_copy(deep=True)
    after.global_items.clear()
    delta = compare(before, after)
    assert delta.mode == 'targeted' and delta.affected_chapter_ordinals == [1]
    assert delta.changed_global_items[0].change == 'removed'


def test_changed_blueprint_chapter_and_no_change_have_distinct_scopes():
    before = _scope()
    assert compare(None, before).mode == 'initial'
    assert compare(before, before).mode == 'unchanged'
    after = before.model_copy(deep=True)
    after.chapters[2] = after.chapters[2].model_copy(update={'subject_sha256': 'b' * 64})
    delta = compare(before, after)
    assert delta.changed_global_items == [] and delta.changed_blueprint_chapters == [2]
    assert delta.affected_chapter_ordinals == [2]


def test_snapshot_pairs_real_item_hash_changes_by_stable_fact_slot(monkeypatch):
    from novel.views import stage2 as view
    from tests.test_stage2_blueprint_workpack import _Store, _book_subjects, _install, _report
    main, blueprints, _ = _book_subjects()
    blueprints.blueprints[1].beats[0].present_char_ids = ['char_002']
    store = _Store(main, blueprints)
    _install(monkeypatch, store, _report())
    pack = view.blueprint_review_workpack('.', store.ctx.sid)
    item = {'kind': 'role', 'item_id': 'role:entities.characters.char_001.role:aaaaaaaaaaaa',
            'subject_path': 'entities.characters.char_001.role', 'subject_sha256': 'a' * 64,
            'subject': '城门守卫', 'current_state': 'PASS', 'formal_evidence': []}
    before = snapshot(pack.model_copy(update={'global_review': {'items': [item]}}))
    changed = copy.deepcopy(item)
    changed.update(item_id='role:entities.characters.char_001.role:bbbbbbbbbbbb',
                   subject_sha256='b' * 64, subject='城门巡查')
    after = snapshot(pack.model_copy(update={'global_review': {'items': [changed]}}))
    delta = compare(before, after)
    assert len(delta.changed_global_items) == 1
    assert delta.changed_global_items[0].change == 'changed'
    assert delta.changed_global_items[0].changed_fields == ['value']
    assert delta.affected_chapter_ordinals == [1]


def test_changed_identity_reopens_a_chapter_with_only_dialogue_reference(monkeypatch):
    from contracts.schema.stage2 import DialogueSemantics
    from novel.views import stage2 as view
    from tests.test_stage2_blueprint_workpack import _Store, _book_subjects, _install, _report
    main, blueprints, _ = _book_subjects()
    store = _Store(main, blueprints)
    _install(monkeypatch, store, _report())
    pack = view.blueprint_review_workpack('.', store.ctx.sid)
    chapter = next(row for row in pack.chapters if row.chapter_ordinal == 2)
    hinge = chapter.beats[0].hinges[0]
    chapter.beats[0].hinges[0] = hinge.model_copy(update={
        'kind': '要害台词', 'dialogue': DialogueSemantics(
            speaker={'kind': 'character', 'char_id': 'char_003'},
            addressees=None, meaning='陌生人请乙关门', speech_act='请求')})
    item = {'kind': 'role', 'item_id': 'role:char_003:old',
            'subject_path': 'entities.characters.char_003.role', 'subject_sha256': 'a' * 64,
            'subject': '路人', 'current_state': 'PASS', 'formal_evidence': []}
    before = snapshot(pack.model_copy(update={'global_review': {'items': [item]}}))
    changed = {**item, 'item_id': 'role:char_003:new', 'subject_sha256': 'b' * 64, 'subject': '守卫'}
    after = snapshot(pack.model_copy(update={'global_review': {'items': [changed]}}))
    delta = compare(before, after)
    assert delta.affected_chapter_ordinals == [2]
    assert delta.changed_global_items[0].affected_references == [f'chapter:2/hinge:{hinge.hinge_id}']
