"""章级 B 的完整设计、必要映射和证据闭包；当前输入必须可连接且能精确恢复。

原 fact/hinge 的连接反例对应到 BeatPlacement、主体映射与共享证据表；
输入身份仍区分本章材料、实际跨章依赖以及未消费的邻章正文。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import hashlib
from types import SimpleNamespace

import pytest

from contracts.schema.stage4 import BindingConcern
from contracts.schema.plot import PlotSubjectRef
from novel.errors import ProvenanceRefused, RefusedByStructure
from novel.stages.stage2.plot_compile import source_digest
from novel.stages.stage4 import plan
from novel.stages.stage4.plot_binding import apply_chapter_resolutions, build_mechanical_baseline
from novel.stages.stage4.plot_design import build_blind_projection, compile_blind_candidate
from tests.stage2_fixtures import plot_blueprints
from tests.test_stage2 import main_text
from tests.test_stage2_grounding import character_preparation, pair  # noqa: F401
from tests.test_stage4_v3_integration import _retain_a_response


def _inputs(pair):
    registry, knowledge = pair
    bp = plot_blueprints()
    first = bp['blueprints'][1]['beats'][0]
    # 同一完整节点有两个有据舞台签名，B 需选择；编号大小不暗示选择。
    first['source_contexts'] = [
        {'evidence_refs': first['evidence_refs'], 'time_layer': 'present',
         'place': '街上', 'present': [{'char_id': 'char_001'}]},
        {'evidence_refs': first['evidence_refs'], 'time_layer': 'present',
         'place': '门口', 'present': [{'char_id': 'char_001'}, {'char_id': 'char_002'}]},
    ]
    global_g = knowledge.model_dump(mode='json', by_alias=True)
    characters = registry.model_dump(mode='json', by_alias=True)
    truth, mapping = build_blind_projection(bp, root_clean_sha1='a' * 40,
                                           global_g=global_g, characters=characters,
                                           main_text=main_text())
    candidate = compile_blind_candidate(_retain_a_response(bp, mapping), mapping, truth=truth)
    up = SimpleNamespace(blueprints=bp, global_g=global_g,
                         characters=characters, main_text=main_text())
    baseline = build_mechanical_baseline(candidate, bp).chapter(2)
    assert len(baseline.ambiguities) == 1 and len(baseline.ambiguities[0].candidates) == 2
    return SimpleNamespace(title='映射反例'), up, candidate, baseline


def _alias(candidate, kind, ref):
    return next(token for token, row in candidate['subject_aliases'].items()
                if row['kind'] == kind and row['ref'] == ref)


def _request(ctx, up, candidate, baseline, prepared=None):
    issue = plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)
    return plan._plan_chapter_request(ctx, prepared or SimpleNamespace(
        subject_sha256='a' * 64, input_sha256='b' * 64), baseline, 2, issue)


def test_binding_prompt_resolves_delivery_and_evidence_without_choosing_for_model(pair):
    ctx, up, candidate, baseline = _inputs(pair)
    frozen = deepcopy(candidate)
    issue = plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)
    payload = json.loads(issue.user)
    assert payload['candidate'] == candidate['output_chapters'][1]
    assert candidate == frozen
    materials = payload['materials']
    ambiguity = payload['ambiguities'][0]
    selected = next(ref for ref, row in materials['source_context_catalog'].items()
                    if row['place'] == '门口')
    assert selected in ambiguity['candidates']
    assert materials['blueprint']['beats'][0]['beat_id'] == ambiguity['beat_ref'] == 'b02_01'
    aliases = payload['mapping']['subject_aliases']
    assert PlotSubjectRef(kind='beat', ref='b02_01').model_dump(mode='json') in aliases.values()
    assert {row['char_id'] for row in payload['mapping']['participant_aliases'].values()} == {'char_001', 'char_002'}
    rows = apply_chapter_resolutions(baseline, [{
        'ambiguity_id': ambiguity['ambiguity_id'], 'selected_refs': [selected],
    }])
    delivery = next(d for scene in rows for d in scene['deliveries'] if d['beat_ref'] == 'b02_01')
    assert delivery['source_context_refs'] == [selected]
    assert delivery['evidence_refs'] == up.blueprints['blueprints'][1]['beats'][0]['evidence_refs']
    assert up.main_text[1]['text'] in [r['text'] for s in materials['source_materials']
                                     for r in s['ranges']]
    assert up.main_text[2]['text'] not in issue.user
    foreign = _alias(candidate, 'beat', 'b03_01')
    for ref in ('b03_01', foreign):
        assert ref not in issue.issued_refs
        assert issue.concern_error(BindingConcern(constraint_code='future_leak',
            scenes=[rows[0]['candidate_scene_id']], reason='不能把邻章材料当作本章呈现。',
            closed_refs=[ref])) is not None


@pytest.mark.parametrize('corruption', [
    'missing_subject_table', 'missing_participant_table', 'missing_beat',
    'missing_participant', 'duplicate_subject', 'duplicate_participant',
    'cross_table_alias', 'cross_table_same_pair', 'foreign_placement',
    'foreign_context', 'foreign_turn', 'foreign_goal', 'foreign_endpoint',
])
def test_binding_prompt_refuses_unjoinable_frozen_evidence(pair, corruption):
    ctx, up, candidate, baseline = _inputs(pair)
    chapter = candidate['output_chapters'][1]
    own = _alias(candidate, 'beat', 'b02_01')
    other = _alias(candidate, 'beat', 'b03_01')
    person = _alias(candidate, 'character', 'char_001')
    if corruption == 'missing_subject_table':
        del candidate['subject_aliases']
    elif corruption == 'missing_participant_table':
        del candidate['participant_aliases']
    elif corruption == 'missing_beat':
        del candidate['subject_aliases'][own]
    elif corruption == 'missing_participant':
        del candidate['participant_aliases'][person]
    elif corruption == 'duplicate_subject':
        candidate['subject_aliases']['a_' + 'f' * 16] = candidate['subject_aliases'][own]
    elif corruption == 'duplicate_participant':
        candidate['participant_aliases']['a_' + 'f' * 16] = deepcopy(candidate['participant_aliases'][person])
    elif corruption in ('cross_table_alias', 'cross_table_same_pair'):
        candidate['participant_aliases'][own] = ({'char_id': 'char_001'}
            if corruption == 'cross_table_alias' else {'char_id': 'b02_01'})
    elif corruption == 'foreign_placement':
        chapter['beat_placements'][0]['beat_alias'] = other
    elif corruption == 'foreign_context':
        chapter['scenes'][0]['context_beat_aliases'] = [other]
    elif corruption == 'foreign_turn':
        chapter['scenes'][0]['turn_beat_alias'] = other
    elif corruption == 'foreign_goal':
        chapter['chapter_goal']['subject_aliases'] = [other]
    else:
        # 端点票只在确需显式绑定时下发；伪造的未登记端点一律不可连接。
        chapter['anchor_bindings'].append(
            {'endpoint_alias': 'a_' + 'f' * 16, 'scene_ref': 's01'})
    with pytest.raises((ProvenanceRefused, RefusedByStructure)):
        plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)


def test_stage_only_participant_is_issued_without_invented_opaque_alias(pair):
    ctx, up, candidate, baseline = _inputs(pair)
    up.characters['characters']['char_009'] = deepcopy(up.characters['characters']['char_001'])
    up.blueprints['blueprints'][1]['beats'][0]['source_contexts'][0]['present'].append({'char_id': 'char_009'})
    issue = plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)
    material = json.loads(issue.user)
    assert 'char_009' in issue.issued_refs
    assert 'char_009' not in {row['char_id'] for row in material['mapping']['participant_aliases'].values()}
    assert any(any(actor['char_id'] == 'char_009' for actor in row['present'])
               for row in material['materials']['source_context_catalog'].values())


def test_binding_request_identity_does_not_reuse_old_input_contract(pair, monkeypatch):
    ctx, up, candidate, baseline = _inputs(pair)
    current = _request(ctx, up, candidate, baseline)
    monkeypatch.setattr(plan, 'STAGE4_BINDING_INPUT_CONTRACT', 'old-input-contract')
    previous = _request(ctx, up, candidate, baseline)
    assert current.input_sha256 != previous.input_sha256
    assert current.user != previous.user


@pytest.mark.parametrize('change', ['content', 'participants', 'time_layer', 'place', 'presence', 'evidence'])
def test_binding_request_invalidates_changed_material_evidence(pair, change):
    ctx, up, candidate, baseline = _inputs(pair)
    before = _request(ctx, up, candidate, baseline)
    bp = up.blueprints['blueprints'][1]
    beat = bp['beats'][0]
    if change == 'content':
        beat['content'] += '她打算向母亲问明来信。'
    elif change == 'participants':
        beat['participants'] = [{'char_id': 'char_001'}]
    elif change in ('time_layer', 'place', 'presence'):
        key = {'presence': 'present'}.get(change, change)
        beat['source_contexts'][0][key] = (
            [] if change == 'presence' else 'recall' if change == 'time_layer' else '院内')
    else:
        anchor = bp['evidence_anchors'][0]
        anchor['end'] -= 1
    current = build_mechanical_baseline(candidate, up.blueprints).chapter(2)
    after = _request(ctx, up, candidate, current)
    assert after.input_sha256 != before.input_sha256


@pytest.mark.parametrize('changed', [
    'foreign_text', 'foreign_beat', 'foreign_candidate', 'book_identity',
    'own_source', 'own_beat', 'own_candidate', 'consumed_foreign_evidence', 'candidates',
])
def test_binding_request_changes_only_for_actual_materials_and_boundary(pair, changed):
    ctx, up, candidate, baseline = _inputs(pair)
    prepared = SimpleNamespace(subject_sha256='a' * 64, input_sha256='b' * 64)
    before = _request(ctx, up, candidate, baseline, prepared)
    if changed == 'foreign_text':
        up.main_text[2]['text'] += '\n\n未消费的院外微风。'
        up.blueprints['blueprints'][2]['source_sha256'] = source_digest(up.main_text[2]['text'])
    elif changed == 'foreign_beat':
        up.blueprints['blueprints'][2]['beats'][0]['content'] += '未消费的动作修饰。'
    elif changed == 'foreign_candidate':
        candidate['output_chapters'][2]['chapter_rewrite_brief'] += '别章说明'
    elif changed == 'book_identity':
        prepared.subject_sha256, prepared.input_sha256 = 'c' * 64, 'd' * 64
    elif changed == 'own_source':
        up.main_text[1]['text'] += '\n\n第三日动身。'
        up.blueprints['blueprints'][1]['source_sha256'] = source_digest(up.main_text[1]['text'])
    elif changed == 'own_beat':
        up.blueprints['blueprints'][1]['beats'][0]['content'] += '她注意到母亲。'
    elif changed == 'own_candidate':
        candidate['output_chapters'][1]['scenes'][0]['purpose'] = '发问'
    elif changed == 'consumed_foreign_evidence':
        # 知识链接退役后，本章实际消费的外部 owner 证据是人物登记；
        # 改它必须改变章输入身份（对照 foreign_* 的未消费证据）。
        evidence_ref = up.characters['characters']['char_002']['aliases'][0]['evidence_refs'][0]
        proof = up.characters['evidence_anchors'][evidence_ref]
        proof['end'] -= 1
        source = next(row['text'] for row in up.main_text
                      if row['chapter_ordinal'] == proof['chapter_ordinal'])
        proof['text_sha256'] = hashlib.sha256(
            source[proof['start']:proof['end']].encode('utf-8')).hexdigest()
    else:
        ambiguity = baseline.ambiguities[0]
        baseline = replace(baseline, ambiguities=(replace(
            ambiguity, candidates=ambiguity.candidates[:1]),))
    after = _request(ctx, up, candidate, baseline, prepared)
    should_change = changed in {'own_source', 'own_beat', 'own_candidate', 'consumed_foreign_evidence', 'candidates'}
    assert (after.input_sha256 != before.input_sha256) is should_change
    if not should_change:
        assert (after.user, after.system) == (before.user, before.system)
