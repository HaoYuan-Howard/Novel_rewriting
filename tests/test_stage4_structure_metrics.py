#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 结构读数的数学口径；不经报告字符串做第二份计算。"""
from __future__ import annotations

import copy

import pytest

from novel.stages.stage4.measure import structure_metrics_of


def _atom(fact_id: str, beat: str, start: int, *, obligation: str = 'land') -> dict:
    text = f'证据{fact_id}'
    return {
        'fact_id': fact_id, 'claim': f'事实{fact_id}', 'kind': 'event',
        'obligation': obligation, 'source_beat_ref': beat,
        'source': {
            'text': text, 'start': start, 'end': start + len(text),
            'normalized': text, 'recovery': 'exact',
        },
        'participant_char_ids': [],
    }


def _blueprint() -> dict:
    return {
        'chapter_ordinal': 1,
        'fact_atoms': [
            _atom('f01_001', 'b01_01', 0),
            _atom('f01_002', 'b01_01', 20),
            _atom('f01_003', 'b01_02', 40),
            _atom('f01_004', 'b01_03', 60),
            _atom('f01_005', 'b01_03', 80),
            _atom('f01_006', 'b01_04', 100),
        ],
        'causal_edges': [{'before': 'f01_001', 'after': 'f01_002'}],
    }


def _goal(ref: str) -> dict:
    return {
        'intent': 'open',
        'fact_refs': [{'kind': 'fact_atom', 'ref': ref, 'chapter': 1}],
    }


def _chapter(groups: list[list[str]] | None = None, *,
             opening: str = 'f01_001', ending: str = 'f01_006') -> dict:
    groups = groups or [
        ['f01_001', 'f01_002'], ['f01_003'],
        ['f01_004', 'f01_005'], ['f01_006'],
    ]
    return {
        'output_ordinal': 1,
        'open_hook_goal': _goal(opening),
        'end_hook_goal': _goal(ending),
        'scenes': [
            {'slot': f's{index:02d}', 'land_fact_refs': refs}
            for index, refs in enumerate(groups, 1)
        ],
    }


def test_identity_partition_and_order_are_reported_with_real_denominators():
    got = structure_metrics_of(_blueprint(), _chapter())

    assert got['state'] == 'ok' and got['mapping'] == {
        'state': 'ok', 'land_facts': 6, 'mapped_facts': 6, 'plan_scenes': 4,
    }
    assert got['partition'] == {
        'state': 'ok',
        'all_fact_pairs': 15,
        'source_together_pairs': 2,
        'source_apart_pairs': 13,
        'preserved_together_pairs': 2,
        'split_pairs': 0,
        'merged_pairs': 0,
        'preserved_apart_pairs': 13,
        'changed_pairs': 0,
        'distance': 0.0,
        'split_rate': 0.0,
        'merge_rate': 0.0,
    }
    assert got['order']['concordant_pairs'] == 13
    assert got['order']['inverted_pairs'] == 0
    assert got['order']['same_scene_ties'] == 2
    assert got['order']['source_comparable_pairs'] == 15
    assert got['adjacency']['source_adjacency_edges'] == 5
    assert got['adjacency']['same_scene'] == 2
    assert got['adjacency']['forward_adjacent'] == 3
    assert got['adjacency']['directional_retention_rate'] == 1.0
    assert got['boundary']['changed'] is False
    assert got['boundary']['source_groups'] == got['boundary']['plan_groups'] == 4
    assert got['noncontiguous']['noncontiguous_scenes'] == 0
    assert got['noncontiguous']['testable_scenes'] == 2
    assert got['anchors']['opening']['declared_goal']['offset'] == 0
    assert got['anchors']['opening']['scene_payload']['excludes_source_edge'] is False
    assert got['anchors']['ending']['declared_goal']['offset'] == 0
    assert got['eligibility']['reorderable'] is True
    assert got['eligibility']['causally_incomparable_pairs'] == 13


def test_same_scene_count_can_hide_changed_boundaries_and_reversed_order():
    chapter = _chapter(
        [['f01_003', 'f01_001'], ['f01_002'],
         ['f01_005', 'f01_006'], ['f01_004']],
        opening='f01_003', ending='f01_004',
    )
    got = structure_metrics_of(_blueprint(), chapter)

    # 源分区和新分区都是四组；只比较个数会假阴。
    assert got['boundary']['source_groups'] == got['boundary']['plan_groups'] == 4
    assert got['boundary']['group_count_changed'] is False
    assert got['boundary']['changed'] is True
    assert got['partition']['split_pairs'] == 2
    assert got['partition']['merged_pairs'] == 2
    assert got['partition']['changed_pairs'] == 4
    assert got['order']['inverted_pairs'] > 0
    assert (got['adjacency']['reversed_adjacent']
            + got['adjacency']['reversed_separated']) > 0
    assert got['adjacency']['directional_retention_rate'] < 1.0

    assert got['noncontiguous']['noncontiguous_scenes'] == 1
    assert got['noncontiguous']['witnesses'] == [
        {'scene': 's01', 'source_runs': [[0, 0], [2, 2]]},
    ]
    # goal 已声明后移，不等于首场排除了源章首事实；两根轴必须分开。
    assert got['anchors']['opening']['declared_goal']['offset'] == 2
    assert got['anchors']['opening']['declared_goal']['migrated'] is True
    assert got['anchors']['opening']['scene_payload']['excludes_source_edge'] is False
    assert got['anchors']['ending']['declared_goal']['offset'] == 2
    assert got['anchors']['ending']['scene_payload']['excludes_source_edge'] is True


def test_a_contiguous_three_fact_block_is_not_noncontiguous_regrouping():
    got = structure_metrics_of(
        _blueprint(),
        _chapter([
            ['f01_001', 'f01_002', 'f01_003'], ['f01_004'],
            ['f01_005'], ['f01_006'],
        ]),
    )

    # f01_001 与 f01_003 在扁平数组里“不相邻”，但中间事实也在同场；
    # 它是一段连续源区间，不得像旧 pair 口径那样报成真正的跨区组场。
    assert got['noncontiguous']['testable_scenes'] == 1
    assert got['noncontiguous']['noncontiguous_scenes'] == 0
    assert got['noncontiguous']['extra_source_runs'] == 0


@pytest.mark.parametrize('mutate', ['missing', 'duplicate', 'unknown', 'empty_scene'])
def test_an_incomplete_mapping_makes_every_structure_section_unver(mutate: str):
    chapter = _chapter()
    if mutate == 'missing':
        chapter['scenes'][-1]['land_fact_refs'] = []
    elif mutate == 'duplicate':
        chapter['scenes'][1]['land_fact_refs'].append('f01_001')
    elif mutate == 'unknown':
        chapter['scenes'][1]['land_fact_refs'].append('f01_999')
    else:
        chapter['scenes'].append({'slot': 's05', 'land_fact_refs': []})

    got = structure_metrics_of(_blueprint(), chapter)

    assert got['state'] == 'unver'
    assert got['mapping']['state'] == 'unver'
    for section in (
        'partition', 'order', 'adjacency', 'boundary',
        'noncontiguous', 'anchors', 'eligibility',
    ):
        assert got[section]['state'] == 'unver', (mutate, section, got[section])


def test_source_spans_not_fact_array_or_ids_define_order():
    blueprint = _blueprint()
    baseline = structure_metrics_of(blueprint, _chapter())
    blueprint['fact_atoms'].reverse()
    reordered = structure_metrics_of(blueprint, _chapter())

    for section in (
        'partition', 'order', 'adjacency', 'boundary',
        'noncontiguous', 'anchors', 'eligibility',
    ):
        assert reordered[section] == baseline[section], section


def test_overlapping_evidence_is_one_locus_and_not_an_invented_order():
    blueprint = _blueprint()
    first = blueprint['fact_atoms'][0]['source']
    first.update({'start': 0, 'end': 30, 'text': '长证据', 'normalized': '长证据'})
    nested = blueprint['fact_atoms'][1]['source']
    nested.update({'start': 10, 'end': 20, 'text': '内证据', 'normalized': '内证据'})

    got = structure_metrics_of(blueprint, _chapter())

    assert got['order']['source_incomparable_pairs'] == 1
    assert got['order']['source_comparable_pairs'] == 14
    assert got['adjacency']['source_loci'] == 5
    assert got['adjacency']['source_adjacency_edges'] == 4


def test_reorderable_denominator_uses_the_transitive_causal_closure():
    blueprint = {
        'chapter_ordinal': 1,
        'fact_atoms': [
            _atom('f01_001', 'b01_01', 0),
            _atom('f01_002', 'b01_02', 20, obligation='context'),
            _atom('f01_003', 'b01_03', 40),
        ],
        'causal_edges': [
            {'before': 'f01_001', 'after': 'f01_002'},
            {'before': 'f01_002', 'after': 'f01_003'},
        ],
    }
    chapter = _chapter(
        [['f01_001'], ['f01_003']], opening='f01_001', ending='f01_003')

    got = structure_metrics_of(blueprint, chapter)

    assert got['eligibility']['cross_group_comparable_pairs'] == 1
    assert got['eligibility']['causally_constrained_pairs'] == 1
    assert got['eligibility']['causally_incomparable_pairs'] == 0
    assert got['eligibility']['reorderable'] is False
    assert got['eligibility']['noncontiguous_eligible'] is False


def test_missing_source_span_does_not_erase_the_still_measurable_partition():
    blueprint = _blueprint()
    blueprint['fact_atoms'][0]['source'] = None

    got = structure_metrics_of(blueprint, _chapter())

    assert got['state'] == 'ok' and got['mapping']['state'] == 'ok'
    assert got['partition']['state'] == 'ok'
    assert got['boundary']['state'] == 'ok'
    for section in ('order', 'adjacency', 'noncontiguous', 'anchors', 'eligibility'):
        assert got[section]['state'] == 'unver'


def test_zero_pair_denominators_are_unver_not_green():
    blueprint = {
        'chapter_ordinal': 1,
        'fact_atoms': [_atom('f01_001', 'b01_01', 0)],
        'causal_edges': [],
    }
    chapter = _chapter([['f01_001']], opening='f01_001', ending='f01_001')

    got = structure_metrics_of(blueprint, chapter)

    assert got['mapping']['state'] == 'ok'
    for section in ('partition', 'order', 'adjacency', 'boundary', 'noncontiguous'):
        assert got[section]['state'] == 'unver'
    assert got['anchors']['state'] == 'unver'
    assert got['eligibility']['state'] == 'ok'
    assert got['eligibility']['reorderable'] is False
