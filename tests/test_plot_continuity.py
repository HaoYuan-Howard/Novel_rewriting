"""Continuity Pass 定向测试。"""
from __future__ import annotations

from contracts.schema.plot import PlotChapterResponse
from novel.stages.stage2.plot_continuity import (
    formal_claim_key, prepare_chapter_for_continuity, wire_claim_key,
)


def test_prepare_injects_entry_and_carry_from_prior_materials():
    carried = [{
        'state': {
            'state_id': 'st01_01', 'content': '信仍在袖中', 'global_ref': None,
            'modality': 'world', 'holder': None,
            'established_at': {'kind': 'at_beat', 'beat_ref': 'b01_02'},
            'ended_at': None, 'supersedes_state_refs': [], 'evidence_refs': ['e01_01'],
        },
        'actor_bindings': [],
    }]
    chapter = PlotChapterResponse.model_validate({
        'chapter_ordinal': 2, 'chapter_function': '续',
        'beats': [{
            'beat_key': 'b1', 'content': '她继续赶路。', 'participants': [],
            'evidence': [{'start_para': 1, 'end_para': 1}],
            'source_contexts': [{
                'evidence': [{'start_para': 1, 'end_para': 1}],
                'time_layer': 'present', 'place': None, 'present': [],
            }],
        }],
        'states': [], 'inherited_state_endings': [], 'constraints': [],
        'entry_state_refs': [], 'carry_out_refs': [],
        'actor_bindings': [], 'relation_uses': [],
        'narration': [{
            'grammatical_person': 'third', 'narrator': None, 'focalizer': None,
            'pronoun_surfaces': [], 'beat_refs': [],
            'evidence': [{'start_para': 1, 'end_para': 1}],
        }],
    })
    prepared = prepare_chapter_for_continuity(chapter, carried)
    assert prepared.entry_state_refs == ['st01_01']
    assert prepared.carry_out_refs == ['st01_01']
    assert prepared.states == []


def test_prepare_dedupes_exact_claim_to_prior_state_id():
    carried = [{
        'state': {
            'state_id': 'st01_01', 'content': '信仍在袖中', 'global_ref': None,
            'modality': 'world', 'holder': None,
            'established_at': {'kind': 'at_beat', 'beat_ref': 'b01_02'},
            'ended_at': None, 'supersedes_state_refs': [], 'evidence_refs': ['e01_01'],
        },
        'actor_bindings': [],
    }]
    chapter = PlotChapterResponse.model_validate({
        'chapter_ordinal': 2, 'chapter_function': '续',
        'beats': [{
            'beat_key': 'b1', 'content': '她摸了摸袖中的信。', 'participants': [],
            'evidence': [{'start_para': 1, 'end_para': 1}],
            'source_contexts': [{
                'evidence': [{'start_para': 1, 'end_para': 1}],
                'time_layer': 'present', 'place': None, 'present': [],
            }],
        }],
        'states': [{
            'state_key': 'st1', 'semantic': '信仍在袖中', 'canonical_ref_hint': None,
            'modality': 'world', 'holder': None,
            'established_at': {'kind': 'at_beat', 'beat_ref': 'b1'},
            'ended_at': None, 'supersedes_state_refs': [],
            'evidence': [{'start_para': 1, 'end_para': 1}],
        }],
        'inherited_state_endings': [], 'constraints': [],
        'entry_state_refs': [], 'carry_out_refs': ['st1'],
        'actor_bindings': [], 'relation_uses': [],
        'narration': [{
            'grammatical_person': 'third', 'narrator': None, 'focalizer': None,
            'pronoun_surfaces': [], 'beat_refs': [],
            'evidence': [{'start_para': 1, 'end_para': 1}],
        }],
    })
    assert wire_claim_key(chapter.states[0].model_dump(mode='json')) == formal_claim_key(
        carried[0]['state'])
    prepared = prepare_chapter_for_continuity(chapter, carried)
    assert prepared.states == []
    assert prepared.entry_state_refs == ['st01_01']
    assert prepared.carry_out_refs == ['st01_01']
