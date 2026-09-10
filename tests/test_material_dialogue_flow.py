# -*- coding: utf-8 -*-
"""材料编译 → B 机械归属 → 写手任务 → 校验；合成语料不冒充语义质量验收。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema.stage2 import Blueprint, ChapterBlueprintsResponseV32, Hinge
from contracts.schema.stage4 import CtxScene
from novel.stages.stage2 import checks as stage2_checks, normalize
from novel.stages.stage2.compile_v2 import canonical_ir_projection_v32, compile_v32_refs
from novel.stages.stage4.baseline import build_mechanical_baseline
from novel.stages.stage4.beats import beat_index, material_tasks, verbatim_pieces
from novel.stages.stage4.context import _resolve_scenes
from novel.stages.stage5 import checks, measure, prompts
from tests.factories import stage5_book


SOURCE = ('宋宁对周衡说：“明日卯时，带三两银子到西门见我。”\n\n'
          '周衡点头答应，又提醒宋宁：“门口要报出 A-17，少一个符号都不放行。”')
MAIN = [{'chapter_ordinal': 1, 'text': SOURCE}]
NAMES = {'char_001': '宋宁', 'char_002': '周衡'}
CLAIMS = ['宋宁要求周衡明日卯时携三两银子在西门会面', '周衡答应宋宁的约定', '门口凭口令 A-17 放行']


def _party(cid):
    return {'kind': 'character', 'char_id': cid}


def _response():
    def atom(index, evidence):
        return {'atom_key': f'a{index:02}', 'claim': CLAIMS[index - 1],
                'kind': 'event', 'obligation': 'land', 'evidence_candidate_id': evidence,
                'participant_char_ids': list(NAMES)}

    def dialogue(phrase, index, speaker, target, act):
        return {'kind': '要害台词', 'phrase': phrase, 'source_fact_refs': [f'a{index:02}'],
                'dialogue': {'speaker': _party(speaker), 'addressees': [_party(target)],
                             'meaning': CLAIMS[index - 1], 'speech_act': act}}

    return {'blueprints': [{
        'chapter_ordinal': 1, 'chapter_function': '订立会面约定并确认入门条件',
        'micro_event_ends': [1, 2],
        'beats': [
            {'end_para': 1, 'time_layer': 'T_PRESENT', 'place': '院内',
             'present_char_ids': list(NAMES), 'fact_atoms': [atom(1, 'e001')],
             'hinges': [dialogue('明日卯时，带三两银子到西门见我。', 1, 'char_001', 'char_002', '请求'),
                        {'kind': '硬数字', 'phrase': '三两银子', 'source_fact_refs': ['a01']}]},
            {'end_para': 2, 'time_layer': 'T_PRESENT', 'place': '院内',
             'present_char_ids': list(NAMES), 'fact_atoms': [atom(2, 'e002'), atom(3, 'e002')],
             'hinges': [dialogue('周衡点头答应', 2, 'char_002', 'char_001', '承诺'),
                        {'kind': '字面例外', 'phrase': 'A-17', 'source_fact_refs': ['a03'],
                         'literal': {'kind': 'code', 'reason': '守门人按这个符号序列决定是否放行'}}]},
        ],
        'causal_edges': [{'before': 'a01', 'after': 'a02'}], 'narrative_relations': [],
        'entry_state_refs': [], 'ending_state_refs': ['a02', 'a03'],
        'narration': [{'grammatical_person': 'third', 'narrator_char_id': None,
                      'focalizer_char_id': 'char_001', 'pronoun_surfaces': []}],
    }]}


def _compiled():
    registry = {cid: {'canonical': name, 'role': '本章在场人物', 'name_distinctiveness': 'high',
                      'address': [{'surface': name, 'by': 'narration',
                                   'context': 'reference', 'from_chapter': 1}]}
                for cid, name in NAMES.items()}
    raw = ChapterBlueprintsResponseV32.model_validate(_response()).model_dump(mode='json')
    compiled, accounting = compile_v32_refs(raw, MAIN, registry)
    assert not accounting['verbatim_failures'] and not accounting['fact_atom_failures']
    chapter = Blueprint.model_validate(compiled['blueprints'][0]).model_dump(mode='json')
    return {'blueprints': [chapter]}, registry


def _candidate(bp):
    facts = bp['blueprints'][0]['fact_atoms']
    return {'fact_aliases': {f'F{n}': fact['fact_id'] for n, fact in enumerate(facts, 1)},
            'output_chapters': [{'output_ordinal': 1, 'scenes': [
                {'candidate_scene_id': f'S{n}', 'land_fact_aliases': [f'F{n}'],
                 'context_fact_aliases': []} for n in range(1, 4)]}]}


def _entry():
    bp, registry = _compiled()
    baseline = build_mechanical_baseline(_candidate(bp), bp).chapter(1)
    assert baseline.mechanical
    scenes = []
    for n in range(1, 4):
        scenes.append({
            'slot': f's{n:02}', 'purpose': '完成约定的相应进展', 'role': 'setup', 'delivery': 'scene',
            'transition': 'start' if n == 1 else 'continue', 'budget_chars': 150,
            'land_fact_refs': [f'f01_{n:03}'], 'context_fact_refs': [], 'turn_fact_ref': f'f01_{n:03}',
            'entry_state_refs': [], 'exit_state_refs': [f'f01_{n:03}'],
            'stage_refs': baseline.scene_stage_refs[f'S{n}'],
            'material_refs': baseline.scene_material_refs[f'S{n}'],
        })
    atoms = {row['fact_id']: row for row in bp['blueprints'][0]['fact_atoms']}
    resolved, unknown, withheld = _resolve_scenes({'scenes': scenes}, beat_index(bp, MAIN), atoms, NAMES)
    assert not unknown and not withheld
    for scene in resolved:
        scene['knowledge'] = []  # 此测试只覆盖材料链；知识披露另由独立关联测试证明。
        CtxScene.model_validate(scene)
    book = stage5_book()
    entry = copy.deepcopy(book.ctx['chapters'][0])
    entry.update(scenes=resolved, output_ordinal=1,
                 disclosure_windows=[], characters=[
                     {'char_id': cid, 'display_name': name, 'role': '本章在场人物'} for cid, name in NAMES.items()])
    entry['semantic_contract'] = {
        'chapter_function': '订立约定并确认入门条件', 'information_delta': [],
        'character_state_delta': [], 'relationship_delta': [], 'reader_knowledge_delta': [],
        'must_land_facts': CLAIMS, 'context_facts': [],
        'causal_edges': [{'before': CLAIMS[0], 'after': CLAIMS[1]}],
        'entry_state': [], 'ending_state': CLAIMS[1:],
    }
    for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
        entry[key] = {'intent': 'advance', 'facts': CLAIMS}
    book.source_text = {1: SOURCE}
    return bp, registry, entry, book


def test_current_response_roundtrip_preserves_fact_ownership_and_semantics():
    bp, registry = _compiled()
    chapter = bp['blueprints'][0]
    response = canonical_ir_projection_v32(chapter, SOURCE)
    ChapterBlueprintsResponseV32.model_validate({'blueprints': [response]})
    again, _ = compile_v32_refs({'blueprints': [response]}, MAIN, registry)
    assert Blueprint.model_validate(again['blueprints'][0]) == Blueprint.model_validate(chapter)
    outcome = next(row for row in stage2_checks.check(global_g={'entities': {'characters': registry}},
        blueprints=bp, main_text=MAIN) if row.rule == 'material_contract')
    assert not outcome.findings


def test_materials_in_same_source_beat_bind_by_explicit_fact_not_beat():
    bp, _ = _compiled()
    baseline = build_mechanical_baseline(_candidate(bp), bp).chapter(1)
    assert baseline.mechanical
    assert baseline.scene_material_refs['S2'] == ('h01_003',)
    assert baseline.scene_material_refs['S3'] == ('h01_004',)
    changed = copy.deepcopy(bp)
    changed['blueprints'][0]['beats'][1]['hinges'][0]['source_fact_refs'].append('f01_003')
    after = build_mechanical_baseline(_candidate(bp), changed).chapter(1)
    ambiguity = next(row for row in after.ambiguities if row.material_ref == 'h01_003')
    assert ambiguity.kind == 'material_multi_scene' and ambiguity.candidates == ('S2', 'S3')
    assert after.binding_rows() is None


@pytest.mark.parametrize('change', ['facts', 'meaning', 'speaker', 'addressees', 'literal', 'start', 'end'])
def test_material_dependency_change_invalidates_landed_plan_but_not_design(tmp_path, change):
    from pydantic import RootModel
    from novel.consumption import stage4_design_blueprints, stage4_context_blueprints
    from novel.store import ArtifactStore
    from tests.test_store import _book, _plan_ctx, _plan, _write_payload, PLAN_REL
    from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS
    bp, _ = _compiled()
    before = copy.deepcopy(bp)
    base = _book(root=tmp_path)
    book = base / '书名_123'
    _write_payload(book, STAGE2_CHAPTER_BLUEPRINTS, bp)
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    store.read(RootModel[dict], STAGE2_CHAPTER_BLUEPRINTS)
    store.write(PLAN_REL, _plan(ctx))
    assert store.freshness(PLAN_REL).state == 'fresh'
    hinge = bp['blueprints'][0]['beats'][0]['hinges'][0]
    if change == 'facts':
        hinge['source_fact_refs'].append('f01_002')
    elif change == 'meaning':
        hinge['dialogue']['meaning'] = '改为询问对方能否携银到西门'
    elif change == 'speaker':
        hinge['dialogue']['speaker'] = None
    elif change == 'addressees':
        hinge['dialogue']['addressees'] = None
    elif change == 'literal':
        bp['blueprints'][0]['beats'][1]['hinges'][1]['literal']['reason'] = '按密码精确验明身份'
    else:
        hinge['source'][change] += 1
    _write_payload(book, STAGE2_CHAPTER_BLUEPRINTS, bp)
    report = store.freshness(PLAN_REL)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    assert stage4_design_blueprints(before) == stage4_design_blueprints(bp)
    assert stage4_context_blueprints(before) != stage4_context_blueprints(bp)


@pytest.mark.parametrize('broken', ['missing_refs', 'foreign_ref', 'wrong_kind', 'unknown_speaker'])
def test_material_contract_refuses_missing_or_fabricated_dependencies(broken):
    bp, registry = _compiled()
    hinge = bp['blueprints'][0]['beats'][0]['hinges'][0]
    if broken == 'missing_refs':
        hinge.pop('source_fact_refs')
    elif broken == 'foreign_ref':
        hinge['source_fact_refs'] = ['f02_001']
    elif broken == 'wrong_kind':
        hinge['kind'] = '字面例外'
    else:
        hinge['dialogue']['speaker']['char_id'] = 'char_999'
    outcomes = stage2_checks.check(global_g={'entities': {'characters': registry}}, blueprints=bp, main_text=MAIN)
    rule = 'present_ids_dangling' if broken == 'unknown_speaker' else 'material_contract'
    assert next(row for row in outcomes if row.rule == rule).findings


def test_source_revalidation_keeps_material_identity_and_semantic_obligations():
    bp, _ = _compiled()
    chapter = bp['blueprints'][0]
    hinge = chapter['beats'][0]['hinges'][0]
    previous = copy.deepcopy(hinge)
    hinge['source']['start'] += 1
    accounting = normalize.normalize_verbatim([chapter], {1: SOURCE})
    assert accounting['verbatim_failures'] and hinge['source'] is None
    assert {key: value for key, value in hinge.items() if key != 'source'} == {
        key: value for key, value in previous.items() if key != 'source'}
    Hinge.model_validate(hinge)


def test_writer_material_uses_dialogue_semantics_and_renders_each_fact_once():
    bp, _, entry, book = _entry()
    material = prompts.build_material(entry, book)
    assert '明日卯时，带三两银子到西门见我。' not in material.given
    assert '周衡点头答应' not in material.given
    assert all(material.given.count(claim) == 1 for claim in CLAIMS)
    assert '宋宁 → 周衡' in material.given and '话语作用：请求' in material.given
    assert 'A-17' in material.given and '三两银子' in material.given
    hinges = [h for b in bp['blueprints'][0]['beats'] for h in b['hinges']]
    assert verbatim_pieces(hinges)[0]['verbatim'] == ['A-17']
    assert measure.mandated_verbatim(entry) == 'A-17'
    revised = prompts.revise_user(material, '上一版完整正文。', [])
    assert '明日卯时，带三两银子到西门见我。' not in revised


def test_dialogue_paraphrase_never_causes_quote_repair_but_stays_semantically_unverified():
    _, _, entry, book = _entry()
    body = '宋宁约周衡次日卯时在西门碰面，叮嘱他携银三两。周衡应允，并交代进门报 A-17。'
    findings = checks.fidelity_findings(body, entry, book, given=prompts.build_material(entry, book).given)
    assert not any(row.rule in {'verbatim_missing', 'keep_value_missing'} for row in findings)
    obligations = [row for row in findings if row.rule == 'dialogue_semantics_unver']
    assert len(obligations) == 2 and all(row.sev == 'unver' for row in obligations)


@pytest.mark.parametrize('literal', ['A17', 'a-17', 'A-71', 'A—17'])
def test_plot_literal_requires_exact_symbols_not_normalized_or_near_match(literal):
    _, _, entry, book = _entry()
    findings = checks.fidelity_findings(f'周衡答应带三两银子，进门报 {literal}。', entry, book, given='三两银子 A-17')
    assert any(row.rule == 'verbatim_missing' and 'A-17' in row.evidence for row in findings)


def test_all_numeric_values_are_retained_and_forged_literal_does_not_trigger_paid_repair():
    _, _, entry, book = _entry()
    entry['scenes'][0]['keep_value'] = ['三两银子、五个人']
    book.source_text[1] += '三两银子、五个人。'
    hits = checks.fidelity_findings('带三两银子，报 A-17。', entry, book, given='三两银子、五个人 A-17')
    assert any(row.rule == 'keep_value_missing' for row in hits)
    entry['scenes'][-1]['verbatim'] = ['A17']
    assert checks.material_anchor_findings(entry, book)
    assert not any(row.rule == 'verbatim_missing' for row in checks.fidelity_findings(
        '未写伪造口令。', entry, book, given='A17'))


def test_currency_unit_liang_is_not_mistaken_for_the_number_two():
    from novel.core.facts import num_values
    assert num_values('三两银子') == num_values('银三两') == {3}
    _, _, entry, book = _entry()
    hits = checks.fidelity_findings('带四两银子，进门报 A-17。', entry, book, given='三两银子 A-17')
    assert any(row.rule == 'keep_value_missing' for row in hits)


def test_unknown_dialogue_party_is_preserved_instead_of_dropped():
    bp, _ = _compiled()
    hinge = bp['blueprints'][0]['beats'][0]['hinges'][0]
    atoms = {row['fact_id']: row for row in bp['blueprints'][0]['fact_atoms']}
    groups, _, missing = material_tasks([hinge], atoms, {'char_001': '宋宁'})
    assert missing == ['char_002']
    assert groups['dialogue'][0]['addressees'] is None
    assert SOURCE not in json.dumps(groups, ensure_ascii=False)


def test_repeated_context_does_not_create_a_second_material_landing_candidate():
    bp, _ = _compiled()
    candidate = _candidate(bp)
    candidate['output_chapters'][0]['scenes'][2]['context_fact_aliases'] = ['F1', 'F2']
    baseline = build_mechanical_baseline(candidate, bp).chapter(1)
    assert baseline.mechanical and baseline.scene_material_refs['S2'] == ('h01_003',)


def test_plan_guard_uses_same_material_fact_candidates_as_binding_request():
    from novel.stages.stage4 import checks as plan_checks
    from novel.stages.stage4.measure import identity_plan

    bp, _ = _compiled()
    plan = identity_plan(bp, MAIN)
    # 人工和整书 B 也要经过相同归属闸；只改 material_refs，A 的事实落点不变。
    scenes = plan['output_chapters'][0]['scenes']
    material = 'h01_004'
    owner = next(scene for scene in scenes if material in scene['material_refs'])
    assert owner is not scenes[0]
    owner['material_refs'].remove(material)
    scenes[0]['material_refs'].append(material)
    outcomes = plan_checks.check(plan, blueprints=bp, main_text=MAIN)
    hit = next(row for row in outcomes if row.rule == 'fact_ref')
    assert any(material in str(finding.evidence) for finding in hit.findings)


def test_ordinary_source_copy_is_counted_as_overlap_and_literal_keeps_newlines():
    _, _, entry, book = _entry()
    quote = SOURCE.split('\n\n')[0]
    assert measure.overlap_ratio(quote, entry, book.source_text) == 1
    hinge = {'kind': '字面例外', 'source': {'text': '天字甲\n\n地字乙'}}
    assert verbatim_pieces([hinge])[0]['verbatim'] == ['天字甲\n\n地字乙']


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
        assert '宋宁 → 周衡' in candidate.user and 'A-17' in candidate.user
