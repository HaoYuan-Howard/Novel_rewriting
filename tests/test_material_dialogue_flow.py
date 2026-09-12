"""完整对白语义、稀疏字面/数值约束到章级写作的直接消费链。

旧材料所有权与 B 不可修改 A 的反例由 test_plot_binding 继续覆盖；此处保留
对白允许转述、未知施受者、口令符号、数值单位、伪造材料和三个写作入口。
"""
from __future__ import annotations
import copy
import json
import pytest
from pydantic import ValidationError
from contracts.schema.plot import PlotChapterResponse, PlotBlueprint
from contracts.schema.stage4 import CtxChapter
from novel.stages.stage2.plot_compile import compile_chapter, response_projection, recompile_evidence
from novel.stages.stage4.plot_placements import compile_placements
from novel.stages.stage5 import checks, measure, prompts
from tests.factories import stage5_book
from tests.test_plot_blueprint import chapter_response, arrangement

SOURCE = ('宋宁对周衡说：“明日卯时，带三两银子到西门见我。”\n\n'
          '周衡点头答应，又提醒宋宁：“门口要报出 A-17，少一个符号都不放行。”')
CLAIMS = ['宋宁要求周衡次日卯时携三两银子在西门会面。',
          '周衡答应宋宁的约定，并提醒她门口凭口令 A-17 放行。']
NAMES = {'char_001': '宋宁', 'char_002': '周衡'}
ACTORS = {cid: 'actor_' + str(index) * 24 for index, cid in enumerate(NAMES, 1)}


def _compiled():
    raw = chapter_response()
    for index, beat in enumerate(raw['beats']):
        beat['content'] = CLAIMS[index]
        beat['source_contexts'] = []
    raw['constraints'] = [
        {'constraint_key': 'c1', 'kind': 'value', 'target_beat_ref': 'b1',
         'value': '三两银子', 'evidence': [{'start_para': 1, 'end_para': 1}]},
        {'constraint_key': 'c2', 'kind': 'literal', 'target_beat_ref': 'b2',
         'value': 'A-17', 'evidence': [{'start_para': 2, 'end_para': 2}]},
    ]
    return compile_chapter(PlotChapterResponse.model_validate(raw), SOURCE,
                           allowed_char_ids=frozenset(NAMES))


def _entry():
    chapter = _compiled()
    book = stage5_book()
    entry = copy.deepcopy(book.ctx['chapters'][0])
    entry['chapter_rewrite_brief'] = '从约定会面推进到入门口令。'
    entry['beats'] = [{'beat_ref': row.beat_id, 'content': row.content,
                      'participants': [ACTORS[actor.char_id] for actor in row.participants]} for row in chapter.beats]
    entry['states'] = []
    entry['constraints'] = [{'constraint_ref': row.constraint_id, 'kind': row.kind,
                            'value': row.value, 'target_beat_ref': row.target_beat_ref} for row in chapter.constraints]
    entry['semantic_contract'] = {'chapter_function': '约定会面并说明放行条件',
        'beat_refs': ['b01_01', 'b01_02'], 'state_refs': [],
        'constraint_refs': ['c01_01', 'c01_02'], 'entry_state_refs': [], 'carry_out_refs': []}
    entry['scenes'] = [{
        'slot': f's{index:02d}', 'purpose': '完成本场呈现任务', 'role': 'setup',
        'delivery': 'scene', 'transition': 'start' if index == 1 else 'continue',
        'budget_chars': 100, 'deliveries': [{'beat_ref': f'b01_{index:02d}',
            'presentation_instruction': None, 'stages': []}], 'context_beat_refs': [],
        'turn_beat_ref': f'b01_{index:02d}', 'state_events': [], 'knowledge_event_refs': [],
        'constraint_refs': [f'c01_{index:02d}'],
    } for index in (1, 2)]
    for name in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
        entry[name]['subject_refs'] = [{'kind': 'beat', 'ref': 'b01_01'}]
    entry['characters'] = [{'actor_ref': ACTORS[cid], 'display_name': value,
                            'surfaces': [value], 'role': '参与约定',
                            'surface_allowed_slots': {value: ['s01', 's02']},
                            'allowed_slots': ['s01', 's02'], 'role_allowed_slots': ['s01', 's02']}
                           for cid, value in NAMES.items()]
    entry['global_subjects'] = []
    entry['narration'] = [{'segment': '全章', 'grammatical_person': 'third',
                           'narrator': ACTORS['char_001'], 'focalizer': None,
                           'pronoun_surfaces': [], 'beat_refs': [], 'allowed_slots': ['s01', 's02']}]
    CtxChapter.model_validate(entry)
    book.ctx['chapters'] = [entry]
    book.source_text = {1: SOURCE}
    book.characters = {cid: {'display_name': value, 'role': '参与约定', 'name_distinctiveness': 'high'}
                       for cid, value in NAMES.items()}
    return chapter, NAMES, entry, book


def test_current_response_roundtrip_preserves_complete_dialogue_and_exact_constraints():
    chapter = _compiled()
    restored = compile_chapter(PlotChapterResponse.model_validate(response_projection(
        chapter.model_dump(mode='json'), SOURCE)), SOURCE, allowed_char_ids=frozenset(NAMES))
    assert restored == chapter
    assert [row.content for row in restored.beats] == CLAIMS
    assert recompile_evidence(chapter, SOURCE) == chapter


def test_writer_material_renders_each_complete_beat_once_without_copying_source_dialogue():
    _, _, entry, book = _entry()
    material = prompts.build_material(entry, book)
    assert all(material.given.count(claim) == 1 for claim in CLAIMS)
    assert '明日卯时，带三两银子到西门见我。' not in material.given
    assert 'A-17' in material.given and '三两银子' in material.given
    assert measure.mandated_verbatim(entry) == 'A-17'
    assert all(prompts.revise_user(material, '上一版完整正文。', []).count(claim) == 1 for claim in CLAIMS)


def test_dialogue_paraphrase_is_allowed_but_full_meaning_remains_semantically_unverified():
    _, _, entry, book = _entry()
    body = '宋宁约周衡次日卯时在西门碰面，叮嘱他携银三两。周衡应允，并交代进门报 A-17。'
    findings = checks.fidelity_findings(body, entry, book, given=prompts.build_material(entry, book).given)
    assert not any(row.rule in {'verbatim_missing', 'keep_value_missing'} for row in findings)
    semantics = checks.segment_fact_findings([{'slot': 's01', 'text': body}], entry, book)
    assert len(semantics) == 1 and semantics[0].sev == 'unver'
    assert semantics[0].evidence == ['b01_01', 'b01_02']


@pytest.mark.parametrize('literal', ['A17', 'a-17', 'A-71', 'A—17'])
def test_plot_literal_requires_exact_symbols_not_normalized_or_near_match(literal):
    _, _, entry, book = _entry()
    rows = checks.fidelity_findings(f'周衡答应带三两银子，进门报 {literal}。', entry, book, given='三两银子 A-17')
    assert any(row.rule == 'verbatim_missing' and 'A-17' in row.evidence for row in rows)


def test_all_numeric_values_are_retained_and_forged_literal_does_not_trigger_paid_repair():
    _, _, entry, book = _entry()
    entry['constraints'][0]['value'] = '三两银子、五个人'
    book.source_text[1] += '三两银子、五个人。'
    rows = checks.fidelity_findings('带三两银子，报 A-17。', entry, book, given='三两银子、五个人 A-17')
    assert any(row.rule == 'keep_value_missing' for row in rows)
    entry['constraints'][1]['value'] = 'A17'
    assert checks.material_anchor_findings(entry, book)
    assert not any(row.rule == 'verbatim_missing' for row in checks.fidelity_findings(
        '未写伪造口令。', entry, book, given='A17'))


def test_currency_unit_liang_is_not_mistaken_for_the_number_two():
    from novel.core.facts import num_values
    assert num_values('三两银子') == num_values('银三两') == {3}
    _, _, entry, book = _entry()
    assert any(row.rule == 'keep_value_missing' for row in checks.fidelity_findings(
        '带四两银子，进门报 A-17。', entry, book, given='三两银子 A-17'))


def test_unknown_dialogue_party_stays_explicit_in_content_not_guessed_by_compiler():
    raw = response_projection(_compiled().model_dump(mode='json'), SOURCE)
    raw['beats'][0]['content'] = '宋宁提出会面要求，原文未明确听者身份。'
    raw['beats'][0]['participants'] = [{'char_id': 'char_001'}]
    chapter = compile_chapter(PlotChapterResponse.model_validate(raw), SOURCE,
                               allowed_char_ids=frozenset(NAMES))
    assert [actor.char_id for actor in chapter.beats[0].participants] == ['char_001']
    assert '未明确听者身份' in chapter.beats[0].content


def test_repeated_context_does_not_create_a_second_delivery_or_constraint():
    chapter = _compiled()
    plan = arrangement().model_dump(mode='json')
    plan['scenes'][1]['context_beat_refs'] = ['b01_01']
    from contracts.schema.plot import PlotChapterArrangement
    compiled = compile_placements(chapter, PlotChapterArrangement.model_validate(plan))
    assert compiled.scene_beats == {'s01': ('b01_01',), 's02': ('b01_02',)}
    assert len([row for row in compiled.bindings if row.kind == 'constraint']) == 2


def test_ordinary_source_copy_is_counted_as_overlap_and_literal_keeps_newlines():
    _, _, entry, book = _entry()
    quote = SOURCE.split('\n\n')[0]
    assert measure.overlap_ratio(quote, entry, book.source_text) == 1
    entry['constraints'][1]['value'] = '天字甲\n\n地字乙'
    assert measure.mandated_verbatim(entry) == '天字甲\n\n地字乙'


def test_all_three_writer_entry_modes_use_current_compact_material():
    from contracts.schema.stage5 import ReviewerRewriteRequest, reviewer_rewrite_request_sha256
    from novel.stages.stage5.write import prepare_chapter_candidate

    _, _, entry, book = _entry()
    payload = {
        'version': 1, 'kind': 'stage5-reviewer-rewrite-request-v1',
        'actor': '合成 Reviewer', 'reason': '合成请求，仅验写手准备入口',
        'review_rel': 'stage5/review_1.json', 'review_sha256': 'a' * 64,
        'unresolved_trim_items': [{'output_ordinal': 1, 'audit_sha256': 'b' * 64,
                                   'chapter_sha256': 'c' * 64}],
        'target_ordinal': 1, 'target_chapter_file_sha256': 'c' * 64,
        'directives': [{'code': 'remove_repetition', 'current_text_evidence': '重复语句'}],
        'reviewer_action_record_sha256': 'd' * 64, 'business_sha256': 'e' * 64,
        'reviewer_contract_sha256': 'f' * 64, 'reviewer_session_run_id': 'fixture-session',
        'reviewer_seat_turn_id': 'fixture-seat', 'provider_response_sha256': '1' * 64,
    }
    payload['request_sha256'] = reviewer_rewrite_request_sha256(payload)
    request = ReviewerRewriteRequest.model_validate(payload)
    prepared = [
        prepare_chapter_candidate(entry, book, revision=None),
        prepare_chapter_candidate(entry, book, revision=('重复语句。', [], 'a' * 64)),
        prepare_chapter_candidate(entry, book, revision=None,
            reviewer_rewrite_request=(request, '2' * 64, '重复语句。')),
    ]
    for candidate in prepared:
        assert all(candidate.user.count(claim) == 1 for claim in CLAIMS)
        assert '明日卯时，带三两银子到西门见我。' not in candidate.user
        assert '宋宁要求周衡' in candidate.user and 'A-17' in candidate.user
