"""完整剧情的结构读数：分母、源披露序、共享证据和不可机械证明的边界。"""
from __future__ import annotations

import copy
import pytest
from novel.stages.stage4.measure import structure_metrics_of


def _blueprint():
    return {'chapter_ordinal': 1,
            'beats': [{'beat_id': f'b01_{n:02}', 'content': f'完整剧情{n}',
                       'evidence_refs': [f'e01_{n:02}']} for n in range(1, 7)],
            'evidence_anchors': [{'evidence_id': f'e01_{n:02}',
                                  'start': (n - 1) * 20, 'end': (n - 1) * 20 + 10}
                                 for n in range(1, 7)],
            'constraints': []}


def _chapter(groups=None):
    groups = groups or [[f'b01_{n:02}'] for n in range(1, 7)]
    placements = {}
    for n, group in enumerate(groups, 1):
        for ref in group:
            placements.setdefault(ref, []).append({'scene_ref': f's{n:02}'})
    return {'output_ordinal': 1,
            'scenes': [{'slot': f's{n:02}'} for n in range(1, len(groups) + 1)],
            'beat_placements': [{'beat_ref': ref, 'deliveries': values}
                                for ref, values in placements.items()]}


def test_identity_partition_and_order_are_reported_with_real_denominators():
    got = structure_metrics_of(_blueprint(), _chapter())
    assert got['mapping'] == {'state': 'ok', 'beats': 6, 'mapped_beats': 6,
                              'deliveries': 6, 'distributed_beats': 0, 'plan_scenes': 6}
    assert got['order']['source_comparable_pairs'] == 15
    assert got['order']['inverted_pairs'] == got['order']['interleaved_pairs'] == 0
    assert got['order']['cross_scene_inversion_rate'] == 0
    assert not got['anchors']['opening']['scene_payload']['excludes_source_edge']
    assert not got['anchors']['ending']['scene_payload']['excludes_source_edge']
    # 节点不是原文场景；分区与自由度不再制造数值真相。
    for section in ('partition', 'boundary', 'noncontiguous', 'eligibility'):
        assert got[section]['state'] == 'unver'


def test_same_scene_count_can_hide_changed_boundaries_and_reversed_order():
    baseline = structure_metrics_of(_blueprint(), _chapter())
    reordered = structure_metrics_of(_blueprint(), _chapter(
        [[f'b01_{n:02}'] for n in [3, 1, 2, 6, 5, 4]]))
    assert reordered['mapping']['plan_scenes'] == baseline['mapping']['plan_scenes']
    assert reordered['order']['inverted_pairs'] == 5
    assert reordered['anchors']['opening']['scene_payload']['excludes_source_edge']
    assert reordered['anchors']['ending']['scene_payload']['excludes_source_edge']


def test_a_contiguous_three_beat_block_does_not_invent_source_scene_grouping():
    got = structure_metrics_of(_blueprint(), _chapter([
        ['b01_01', 'b01_02', 'b01_03'], ['b01_04'], ['b01_05'], ['b01_06']]))
    assert got['mapping']['plan_scenes'] == 4
    assert got['order']['inverted_pairs'] == 0
    assert got['noncontiguous']['state'] == 'unver'
    assert got['boundary']['state'] == 'unver'


@pytest.mark.parametrize('mutate', ['missing', 'duplicate', 'unknown', 'empty_scene'])
def test_an_incomplete_mapping_makes_every_structure_section_unver(mutate):
    chapter = _chapter()
    if mutate == 'missing':
        chapter['beat_placements'].pop()
    elif mutate == 'duplicate':
        chapter['beat_placements'].append(copy.deepcopy(chapter['beat_placements'][0]))
    elif mutate == 'unknown':
        chapter['beat_placements'][0]['deliveries'][0]['scene_ref'] = 's99'
    else:
        chapter['scenes'].append({'slot': 's07'})
    got = structure_metrics_of(_blueprint(), chapter)
    assert got['state'] == 'unver'
    for section in ('mapping', 'partition', 'order', 'boundary',
                    'noncontiguous', 'anchors', 'eligibility'):
        assert got[section]['state'] == 'unver'


def test_source_spans_not_beat_array_or_ids_define_order():
    blueprint = _blueprint()
    baseline = structure_metrics_of(blueprint, _chapter())
    blueprint['beats'].reverse()
    blueprint['evidence_anchors'].reverse()
    assert structure_metrics_of(blueprint, _chapter()) == baseline


def test_overlapping_evidence_is_not_an_invented_order():
    blueprint = _blueprint()
    blueprint['evidence_anchors'][0].update(start=0, end=30)
    blueprint['evidence_anchors'][1].update(start=10, end=20)
    got = structure_metrics_of(blueprint, _chapter())
    assert got['order']['source_comparable_pairs'] == 14
    assert got['order']['inverted_pairs'] == 0


def test_causal_closure_is_not_a_complete_denominator_for_narrative_freedom():
    blueprint = _blueprint()
    blueprint['constraints'] = [
        {'kind': 'cause', 'before_beat_ref': f'b01_{n:02}',
         'after_beat_ref': f'b01_{n + 1:02}'} for n in range(1, 6)]
    got = structure_metrics_of(blueprint, _chapter())
    assert got['eligibility']['state'] == 'unver'
    assert '全集' in got['eligibility']['reason']
    assert got['order']['source_comparable_pairs'] == 15


def test_missing_source_span_does_not_erase_the_still_measurable_mapping():
    blueprint = _blueprint()
    blueprint['evidence_anchors'][0]['start'] = None
    got = structure_metrics_of(blueprint, _chapter())
    assert got['state'] == got['mapping']['state'] == 'ok'
    assert got['mapping']['mapped_beats'] == 6
    for section in ('order', 'partition', 'boundary', 'noncontiguous', 'anchors', 'eligibility'):
        assert got[section]['state'] == 'unver'


def test_zero_pair_denominators_are_uncomputed_not_zero_rates():
    blueprint = _blueprint()
    blueprint['beats'] = blueprint['beats'][:1]
    blueprint['evidence_anchors'] = blueprint['evidence_anchors'][:1]
    got = structure_metrics_of(blueprint, _chapter([['b01_01']]))
    assert got['mapping']['state'] == 'ok'
    assert got['order']['source_comparable_pairs'] == 0
    assert got['order']['cross_scene_inversion_rate'] is None
    assert got['eligibility']['state'] == 'unver'


def test_multiple_deliveries_report_interleaving_without_new_source_atoms():
    chapter = _chapter([['b01_01'], ['b01_02'], ['b01_01', 'b01_03'],
                        ['b01_04'], ['b01_05'], ['b01_06']])
    got = structure_metrics_of(_blueprint(), chapter)
    assert got['mapping']['beats'] == 6 and got['mapping']['deliveries'] == 7
    assert got['mapping']['distributed_beats'] == 1
    assert got['order']['interleaved_pairs'] == 2
    assert got['partition']['state'] == 'unver'
