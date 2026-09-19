#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blueprint V11：稀疏 source_contexts 往返、evidence 切片、story_before 去重。"""
from __future__ import annotations

import pytest

from contracts.schema.plot import (
    ActorRef, EvidenceAnchor, NarrationDefault, PlotBeat, PlotBlueprint,
    PlotNarration, PlotRelationConstraint, SourceContext,
)
from novel.stages.stage2.blueprint.compile import dedupe_exact_story_before
from novel.stages.stage2.blueprint.evidence import (
    BlueprintEvidenceError, evidence_ids_of, resolve_evidence, slice_evidence,
)
from novel.stages.stage2.blueprint.narration import (
    derive_narration_default, effective_narration,
)
from novel.stages.stage2.blueprint.source_contexts import (
    compress_beat_contexts, compress_source_context, expand_beat_contexts,
    expand_source_context,
)


def _actor(cid: str, pref: str | None = None) -> ActorRef:
    return ActorRef(char_id=cid, presentation_ref=pref)


def _minimal_chapter(**overrides) -> PlotBlueprint:
    beat = PlotBeat(
        beat_id='b01_01',
        content='甲推门进屋。',
        participants=[_actor('char_001')],
        evidence_refs=['e01_01'],
        source_contexts=None,
    )
    base = dict(
        chapter_ordinal=1,
        source_sha256='a' * 64,
        chapter_function='开篇',
        actor_bindings=[],
        relation_uses=[],
        beats=[beat],
        evidence_anchors=[EvidenceAnchor(
            evidence_id='e01_01', start_para=1, end_para=1, start=0, end=6)],
        states=[],
        inherited_state_endings=[],
        constraints=[],
        entry_state_refs=[],
        carry_out_refs=[],
        narration=[PlotNarration(
            grammatical_person='third',
            narrator=None,
            focalizer=None,
            pronoun_surfaces=[],
            beat_refs=[],
            evidence_refs=['e01_01'],
        )],
    )
    base.update(overrides)
    return PlotBlueprint.model_validate(base)


def test_expand_omit_present_equals_participants_not_guess():
    beat = _minimal_chapter().beats[0]
    # 省略 present → 与 participants 一致
    expanded = expand_source_context(SourceContext(), beat)
    assert expanded['present'] == [{'char_id': 'char_001', 'presentation_ref': None}]
    assert expanded['time_layer'] == 'present'
    assert expanded['evidence_refs'] == ['e01_01']
    # 显式 null → 未知
    unknown = expand_source_context(
        SourceContext.model_validate({'present': None}), beat)
    assert 'present' in SourceContext.model_validate({'present': None}).model_fields_set
    assert unknown['present'] is None
    # 显式 [] → 空场
    empty = expand_source_context(
        SourceContext.model_validate({'present': []}), beat)
    assert empty['present'] == []


def test_compress_expand_roundtrip_preserves_actor_presentation():
    chapter = _minimal_chapter()
    beat = chapter.beats[0]
    full = {
        'evidence_refs': ['e01_01'],
        'time_layer': 'recall',
        'place': '柴房',
        'present': [
            {'char_id': 'char_001', 'presentation_ref': None},
            {'char_id': 'char_002', 'presentation_ref': 'ap01_01'},
        ],
    }
    sparse = compress_source_context(full, beat)
    restored = expand_source_context(
        SourceContext.model_validate(sparse), beat)
    assert restored == full


def test_two_contexts_not_merged_into_one():
    chapter = _minimal_chapter()
    beat = chapter.beats[0]
    rows = [
        {'evidence_refs': ['e01_01'], 'time_layer': 'present', 'place': '堂上',
         'present': [{'char_id': 'char_001', 'presentation_ref': None}]},
        {'evidence_refs': ['e01_01'], 'time_layer': 'recall', 'place': '柴房',
         'present': [{'char_id': 'char_001', 'presentation_ref': None}]},
    ]
    sparse = compress_beat_contexts(rows, beat)
    assert sparse is not None and len(sparse) == 2
    rebuilt = PlotBeat(
        beat_id=beat.beat_id,
        content=beat.content,
        participants=beat.participants,
        evidence_refs=beat.evidence_refs,
        source_contexts=[SourceContext.model_validate(r) for r in sparse],
    )
    assert len(expand_beat_contexts(rebuilt)) == 2


def test_omit_list_synthesizes_exactly_one_default_context():
    beat = _minimal_chapter().beats[0]
    assert len(expand_beat_contexts(beat)) == 1
    assert expand_beat_contexts(beat)[0]['time_layer'] == 'present'


def test_evidence_slice_does_not_leak_whole_table():
    chapter = _minimal_chapter(
        beats=[
            PlotBeat(
                beat_id='b01_01', content='A', participants=[_actor('char_001')],
                evidence_refs=['e01_01'], source_contexts=None),
            PlotBeat(
                beat_id='b01_02', content='B', participants=[_actor('char_001')],
                evidence_refs=['e01_02'], source_contexts=None),
        ],
        evidence_anchors=[
            EvidenceAnchor(evidence_id='e01_01', start_para=1, end_para=1, start=0, end=1),
            EvidenceAnchor(evidence_id='e01_02', start_para=2, end_para=2, start=1, end=2),
        ],
        narration=[PlotNarration(
            grammatical_person='third', narrator=None, focalizer=None,
            pronoun_surfaces=[], beat_refs=[], evidence_refs=['e01_01', 'e01_02'])],
    )
    sliced = slice_evidence(chapter, ['e01_01'])
    assert [r['evidence_id'] for r in sliced] == ['e01_01']
    assert evidence_ids_of(chapter) == frozenset({'e01_01', 'e01_02'})
    with pytest.raises(BlueprintEvidenceError):
        resolve_evidence(chapter, 'e02_01')


def test_story_before_exact_duplicate_removed_adjacent_kept():
    rows = [
        {'kind': 'story_before', 'before_beat_ref': 'b1', 'after_beat_ref': 'b2'},
        {'kind': 'story_before', 'before_beat_ref': 'b1', 'after_beat_ref': 'b2'},
        {'kind': 'story_before', 'before_beat_ref': 'b1', 'after_beat_ref': 'b3'},
        {'kind': 'cause', 'before_beat_ref': 'b1', 'after_beat_ref': 'b2'},
    ]
    out = dedupe_exact_story_before(rows)
    assert len(out) == 3
    assert sum(1 for r in out if r['kind'] == 'story_before') == 2


def test_narration_default_and_effective():
    c1 = _minimal_chapter()
    c2 = _minimal_chapter(
        chapter_ordinal=2,
        beats=[PlotBeat(
            beat_id='b02_01', content='乙离开。', participants=[_actor('char_001')],
            evidence_refs=['e02_01'], source_contexts=None)],
        evidence_anchors=[EvidenceAnchor(
            evidence_id='e02_01', start_para=1, end_para=1, start=0, end=3)],
        narration=[PlotNarration(
            grammatical_person='third', narrator=None, focalizer=None,
            pronoun_surfaces=[], beat_refs=[], evidence_refs=['e02_01'])],
    )
    default = derive_narration_default([c1, c2])
    assert default is not None
    assert default.grammatical_person == 'third'
    empty = c1.model_copy(update={'narration': []})
    rows = effective_narration(empty, narration_default=default)
    assert len(rows) == 1
    assert rows[0].grammatical_person == 'third'


def test_scx_hash_stable_across_sparse_and_dense():
    from novel.stages.stage4.plot_binding import source_context_catalog
    dense = _minimal_chapter(
        beats=[PlotBeat(
            beat_id='b01_01', content='甲推门进屋。',
            participants=[_actor('char_001')],
            evidence_refs=['e01_01'],
            source_contexts=[SourceContext(
                evidence_refs=['e01_01'], time_layer='present',
                place=None, present=[_actor('char_001')])])],
    )
    sparse = _minimal_chapter()  # source_contexts=None → default
    # dense explicit default-equivalent vs omit should share expanded identity
    cat_dense = source_context_catalog(dense)
    cat_sparse = source_context_catalog(sparse)
    assert set(cat_dense) == set(cat_sparse)
