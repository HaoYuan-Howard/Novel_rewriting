#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-45 批次 4：Stage4B 确定性机械基线。

- 干净书：基线完全机械，且绑定行与正式 B 响应（`_bound_b_response`）逐字同构
  ——代码唯一确定的绑定就是模型本来唯一能给出的那份。
- 签名等价的多候选舞台：语义无选择，取规范首项，不算歧义。
- time/place/present 签名不同：`stage_signature_conflict`；材料多场可落：
  `material_multi_scene`；无处可落：`material_unplaceable`；id 跨轮稳定。
"""
from __future__ import annotations

import copy
from typing import Any

from novel.stages.stage4.baseline import build_mechanical_baseline
from tests.test_stage4_agent_boundary import (
    _book, _bound_b_response, _land_design, _seed_current_stage2,
)
from novel import prov


def setup_module(module) -> None:  # noqa: ANN001
    prov_code = prov.code_version


def _blueprint(*beat_rows: tuple[str, str, str, str, list[str]],
               atoms: list[tuple[str, str]]) -> dict[str, Any]:
    """(beat_id, time_layer, place, present, hinges) × N + (fact_id, beat)。"""
    return {'blueprints': [{
        'chapter_ordinal': 1,
        'beats': [
            {'beat_id': beat_id, 'time_layer': layer, 'place': place,
             'present_char_ids': present.split(',') if present else [],
             'hinges': [{'hinge_id': hinge} for hinge in hinges]}
            for beat_id, layer, place, present, hinges in beat_rows
        ],
        'fact_atoms': [
            {'fact_id': fact_id, 'claim': f'{fact_id} 的 claim',
             'kind': 'event', 'obligation': 'land', 'source_beat_ref': beat,
             'participant_char_ids': []}
            for fact_id, beat in atoms
        ],
    }]}


def _candidate(*scenes: tuple[str, list[str]]) -> dict[str, Any]:
    aliases: dict[str, str] = {}
    rows = []
    for index, (scene_id, fact_ids) in enumerate(scenes, start=1):
        scene_aliases = []
        for fact_id in fact_ids:
            alias = f'a_{fact_id}'
            aliases[alias] = fact_id
            scene_aliases.append(alias)
        rows.append({'candidate_scene_id': scene_id,
                     'land_fact_aliases': scene_aliases,
                     'context_fact_aliases': []})
    return {'fact_aliases': aliases, 'output_chapters': [
        {'output_ordinal': 1, 'scenes': rows}]}


def test_identical_signature_stages_are_canonically_resolved() -> None:
    """签名等价的多候选舞台没有真自由度：取规范首项，不产歧义。"""
    blueprints = _blueprint(
        ('b01_01', 'now', '庭院', 'c1', ['h1']),
        ('b01_02', 'now', '正厅', 'c1', ['h2']),
        ('b01_03', 'now', '庭院', 'c1', []),       # 与 b01_01 签名等价
        atoms=[('f_01', 'b01_01'), ('f_02', 'b01_02'), ('f_03', 'b01_03')])
    candidate = _candidate(
        ('cs1', ['f_01', 'f_03']), ('cs2', ['f_02']))
    baseline = build_mechanical_baseline(candidate, blueprints)
    assert baseline.fully_mechanical
    chapter = baseline.chapter(1)
    assert chapter.scene_stage_refs['cs1'] == ('b01_01',)
    assert chapter.binding_rows() == [
        {'candidate_scene_id': 'cs1', 'stage_refs': ['b01_01'],
         'material_refs': ['h1']},
        {'candidate_scene_id': 'cs2', 'stage_refs': ['b01_02'],
         'material_refs': ['h2']},
    ]


def test_signature_conflict_and_material_ambiguities_are_stable() -> None:
    blueprints = _blueprint(
        ('b01_01', 'now', '庭院', 'c1', ['h1']),
        ('b01_02', 'now', '正厅', 'c1', ['h2']),
        ('b01_04', 'recall', '旧宅', 'c1,c2', ['h4']),   # 无人引用：无处可落
        atoms=[('f_01', 'b01_01'), ('f_02', 'b01_02'), ('f_03', 'b01_02')])
    candidate = _candidate(
        ('cs1', ['f_01', 'f_03']), ('cs2', ['f_02']))
    baseline = build_mechanical_baseline(candidate, blueprints)
    kinds = sorted(a.kind for a in baseline.ambiguities)
    assert kinds == [
        'material_multi_scene', 'material_unplaceable',
        'stage_signature_conflict']
    by_kind = {a.kind: a for a in baseline.ambiguities}
    conflict = by_kind['stage_signature_conflict']
    assert conflict.candidates == ('b01_01', 'b01_02')
    assert conflict.candidate_scene_id == 'cs1'
    # cs1 歧义未决：舞台与材料都留空；cs2 机械确定。
    chapter = baseline.chapter(1)
    assert chapter.scene_stage_refs['cs1'] == ()
    assert chapter.scene_stage_refs['cs2'] == ('b01_02',)
    assert chapter.binding_rows() is None
    assert not baseline.fully_mechanical
    # 未决材料的 h1 仍唯一可落 cs1（allowlist 命中）；h2 两场都可落 → multi。
    multi = [a for a in baseline.ambiguities if a.kind == 'material_multi_scene']
    assert len(multi) == 1 and multi[0].material_ref == 'h2'
    # id 由内容决定：同输入同 id，候选集变化则 id 变。
    again = build_mechanical_baseline(candidate, blueprints)
    assert again.ambiguities == baseline.ambiguities
    assert again.ambiguities[0].ambiguity_id == \
        baseline.ambiguities[0].ambiguity_id
    mutated = copy.deepcopy(candidate)
    mutated['output_chapters'][0]['scenes'][0]['land_fact_aliases'] = (
        ['a_f_01'])
    drift = build_mechanical_baseline(mutated, blueprints)
    # 候选集变化 → 冲突歧义消失（h4 无处可落与本场无关，仍保留）。
    assert not any(a.kind == 'stage_signature_conflict'
                   for a in drift.ambiguities)


def test_clean_book_baseline_matches_the_canonical_bound_response(
        tmp_path,
) -> None:
    """真实链条：基线的确定性绑定 ≡ 模型在唯一解下给出的正式绑定。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprints_raw = blueprints.model_dump(by_alias=True, mode='json')
    _design_ctx, candidate = _land_design(book, blueprints_raw)
    candidate_business = candidate.model_dump(
        by_alias=True, mode='json', exclude={'provenance'})
    baseline = build_mechanical_baseline(candidate_business, blueprints_raw)
    assert baseline.fully_mechanical, (
        '夹具书的绑定应当唯一可解；出现歧义说明基线判定过严')
    canonical = _bound_b_response(candidate, blueprints_raw)
    assert baseline.binding_chapters() == canonical['chapters']
