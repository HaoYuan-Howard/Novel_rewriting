#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 原子事实场景编译器的确定性单元测试。"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from contracts.schema.stage4 import BlindSceneResponse, PlanChapters
from novel.errors import RefusedByStructure
from novel.stages.stage4 import checks
from novel.stages.stage4.beats import beat_index, verbatim_pieces
from novel.stages.stage4.measure import (
    adopt_plan_response, computed_structure_change_kinds, identity_plan,
    semantic_contract_of, source_chars_table, structure_metrics_of,
)


TEXT = '甲收到入住邀请。\n乙在门口拒绝甲。\n甲决定留下。'


def _source(fragment: str) -> dict:
    start = TEXT.index(fragment)
    return {'text': fragment, 'start': start, 'end': start + len(fragment)}


def _blueprints() -> dict:
    spans = [_source('甲收到入住邀请。'), _source('乙在门口拒绝甲。'), _source('甲决定留下。')]
    return {'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '建立入住冲突并完成主角入局',
        'fact_atoms': [
            {'fact_id': 'f01_001', 'claim': '甲收到入住邀请', 'kind': 'information',
             'obligation': 'land', 'source_beat_ref': 'b01_01', 'source': spans[0],
             'participant_char_ids': ['char_001']},
            {'fact_id': 'f01_002', 'claim': '乙拒绝甲入住', 'kind': 'event',
             'obligation': 'land', 'source_beat_ref': 'b01_02', 'source': spans[1],
             'participant_char_ids': ['char_001', 'char_002']},
            {'fact_id': 'f01_003', 'claim': '甲决定留下', 'kind': 'character_state',
             'obligation': 'land', 'source_beat_ref': 'b01_03', 'source': spans[2],
             'participant_char_ids': ['char_001']},
        ],
        'causal_edges': [{'before': 'f01_002', 'after': 'f01_003'}],
        'entry_state_refs': [], 'ending_state_refs': ['f01_003'],
        'beats': [
            {'beat_id': 'b01_01', 'span': spans[0], 'time_layer': 'present',
             'place': '门外', 'present_char_ids': ['char_001'],
             'hinges': []},
            {'beat_id': 'b01_02', 'span': spans[1], 'time_layer': 'present',
             'place': '门口', 'present_char_ids': ['char_001', 'char_002'],
             'hinges': [{'hinge_id': 'h01_001', 'kind': '要害台词',
                         'source': _source('拒绝甲')}]},
            {'beat_id': 'b01_03', 'span': spans[2], 'time_layer': 'present',
             'place': '门口', 'present_char_ids': ['char_001'],
             'hinges': []},
        ],
        'narration': [],
    }]}


def _main() -> list[dict]:
    return [{'chapter_ordinal': 1, 'text': TEXT}]


def _goal(intent: str, ref: str) -> dict:
    return {'intent': intent,
            'fact_refs': [{'kind': 'fact_atom', 'ref': ref}]}


def _response() -> dict:
    return {
        'reconstruction_strategy': '先展示拒绝，再补出邀请原因，章尾完成留下的选择。',
        'output_chapters': [{
            'output_ordinal': 1, 'from_source_ordinals': [1],
            'chapter_goal': _goal('G_ADVANCE', 'f01_003'),
            'open_hook_goal': _goal('G_OPEN', 'f01_002'),
            'end_hook_goal': _goal('G_CLOSE', 'f01_003'),
            'structure_decision': {
                'mode': 'reconstruct',
                'reason': '从拒绝切入并重组邀请与选择。',
                'declared_change_kinds': ['entry_anchor', 'fact_grouping'],
            },
            'weight': 'CH_EVEN',
            'chapter_rewrite_brief': '从门口拒绝切入，再补足邀请原因，最后让甲决定留下。',
            'scenes': [
                {'weight': 'W_STRONG', 'purpose': '让拒绝立即发生',
                 'role': 'R_COLD_OPEN', 'delivery': 'D_SCENE', 'transition': 'X_START',
                 'land_fact_refs': ['f01_002'], 'context_fact_refs': [],
                 'material_refs': ['h01_001'], 'turn_fact_ref': 'f01_002',
                 'entry_state_refs': [], 'exit_state_refs': ['f01_002'],
                 'stage_refs': ['b01_02']},
                {'weight': 'W_PEAK', 'purpose': '补出邀请并完成留下的选择',
                 'role': 'R_PAYOFF', 'delivery': 'D_SCENE', 'transition': 'X_EARLIER',
                 'land_fact_refs': ['f01_001', 'f01_003'], 'context_fact_refs': [],
                 'material_refs': [], 'turn_fact_ref': 'f01_003',
                 'entry_state_refs': ['f01_002'], 'exit_state_refs': ['f01_003'],
                 'stage_refs': ['b01_01', 'b01_03']},
            ],
        }],
    }


def _compiled() -> dict:
    plan = _response()
    bps = _blueprints()
    adopt_plan_response(plan, bps, beat_index(bps, _main()))
    return plan


def _findings(plan: dict) -> dict[str, list]:
    return {outcome.rule: list(outcome.findings) for outcome in checks.check(
        plan, blueprints=_blueprints(), main_text=_main())}


def test_beat_index_is_evidence_only_and_has_no_event_payload():
    index = beat_index(_blueprints(), _main())
    assert index['b01_02']['place'] == '门口'
    assert index['b01_02']['source_chars'] == len('乙在门口拒绝甲。')
    assert 'event' not in index['b01_02']


def test_cross_paragraph_material_is_split_without_changing_its_words():
    pieces, unknown = verbatim_pieces([{
        'hinge_id': 'h01_001', 'kind': '要害台词',
        'source': {'text': '第一句。\n\n第二句。'},
    }])
    assert pieces['verbatim'] == ['第一句。', '第二句。'] and unknown == []


def test_plan_response_rejects_every_retired_skeleton_key():
    alias = 'a_0123456789abcdef'
    scene = {
        'weight': 'W_STRONG', 'purpose': '让拒绝立即发生',
        'role': 'R_COLD_OPEN', 'delivery': 'D_SCENE', 'transition': 'X_START',
        'land_fact_aliases': [alias], 'context_fact_aliases': [],
        'turn_fact_alias': alias, 'entry_state_aliases': [],
        'exit_state_aliases': [alias],
    }
    for key, value in (
        ('events', ['旧事件']), ('from_beats', ['b01_01']), ('end_beat', 'b01_01'),
    ):
        raw = {**scene, key: value}
        with pytest.raises(ValidationError, match=key):
            BlindSceneResponse.model_validate(raw)


def test_scene_compiler_decodes_enums_allocates_slots_and_strips_budget_scaffolding():
    chapter = _compiled()['output_chapters'][0]
    assert 'weight' not in chapter
    assert [scene['slot'] for scene in chapter['scenes']] == ['s01', 's02']
    assert all('weight' not in scene for scene in chapter['scenes'])
    assert [scene['transition'] for scene in chapter['scenes']] == ['start', 'earlier']
    assert sum(scene['budget_chars'] for scene in chapter['scenes']) == sum(
        len(row) for row in ('甲收到入住邀请。', '乙在门口拒绝甲。', '甲决定留下。'))
    assert 'semantic_contract' not in chapter
    derived = semantic_contract_of(_blueprints()['blueprints'][0])
    assert derived['information_delta'] == ['f01_001']
    assert derived['character_state_delta'] == ['f01_003']


def test_compiled_scene_order_is_the_new_order_not_the_source_atom_order():
    scenes = _compiled()['output_chapters'][0]['scenes']
    assert scenes[0]['land_fact_refs'] == ['f01_002']
    assert scenes[1]['land_fact_refs'] == ['f01_001', 'f01_003']


def test_scene_compiler_refuses_missing_or_duplicate_land_atoms():
    for mutate in ('missing', 'duplicate'):
        raw = _response()
        if mutate == 'missing':
            raw['output_chapters'][0]['scenes'][1]['land_fact_refs'].remove('f01_001')
        else:
            raw['output_chapters'][0]['scenes'][1]['land_fact_refs'].append('f01_002')
        with pytest.raises(RefusedByStructure, match='缺失=|重复='):
            adopt_plan_response(raw, _blueprints(), beat_index(_blueprints(), _main()))


def test_scene_compiler_refuses_unknown_stage_refs():
    raw = _response()
    raw['output_chapters'][0]['scenes'][0]['stage_refs'] = ['b02_01']
    with pytest.raises(RefusedByStructure, match='stage_refs'):
        adopt_plan_response(raw, _blueprints(), beat_index(_blueprints(), _main()))


def test_identity_plan_is_an_explicit_source_order_baseline_but_uses_scenes():
    chapter = identity_plan(_blueprints(), _main())['output_chapters'][0]
    assert [scene['slot'] for scene in chapter['scenes']] == ['s01', 's02', 's03']
    assert [scene['land_fact_refs'][0] for scene in chapter['scenes']] == [
        'f01_001', 'f01_002', 'f01_003']
    assert not any(key in scene for scene in chapter['scenes']
                   for key in ('events', 'from_beats', 'end_beat'))


def test_structure_dimensions_are_computed_from_the_current_plan_not_persisted():
    chapter = _compiled()['output_chapters'][0]
    before = copy.deepcopy(chapter)
    decision = chapter['structure_decision']
    assert decision['declared_change_kinds'] == ['entry_anchor', 'fact_grouping']
    assert 'computed_change_kinds' not in decision
    metrics = structure_metrics_of(_blueprints()['blueprints'][0], chapter)
    assert computed_structure_change_kinds(metrics) == [
        'entry_anchor', 'fact_grouping', 'disclosure_order',
        'noncontiguous_regroup',
    ]
    assert chapter == before, '现场结构读数不得反向修改计划业务字节'


@pytest.mark.parametrize('retired,mutate', [
    ('reconstruction_strategy', lambda p: p.__setitem__(
        'reconstruction_strategy', '旧策略副本')),
    ('chapter.weight', lambda p: p['output_chapters'][0].__setitem__('weight', 'even')),
    ('semantic_contract', lambda p: p['output_chapters'][0].__setitem__(
        'semantic_contract', {})),
    ('computed_change_kinds', lambda p: p['output_chapters'][0][
        'structure_decision'].__setitem__('computed_change_kinds', [])),
    ('scene.weight', lambda p: p['output_chapters'][0]['scenes'][0].__setitem__(
        'weight', 'mid')),
    ('participant_refs', lambda p: p['output_chapters'][0]['scenes'][0].__setitem__(
        'participant_refs', [])),
    ('FactRef.chapter', lambda p: p['output_chapters'][0]['chapter_goal'][
        'fact_refs'][0].__setitem__('chapter', 1)),
    ('goal.rationale', lambda p: p['output_chapters'][0]['chapter_goal'].__setitem__(
        'rationale', '旧展示理由')),
    ('provenance.params', lambda p: p['_provenance'].__setitem__('params', {})),
])
def test_plan_v4_rejects_every_retired_persistent_field(retired, mutate):
    payload = copy.deepcopy(_compiled())
    payload.pop('reconstruction_strategy')
    payload['_provenance'] = {
        'code': 'test', 'root_clean_sha1': '1' * 40, 'inputs': {},
        'origin': 'human', 'blind_candidate_sha256': None,
        'evidence_binding_sha256': None,
    }
    mutate(payload)
    with pytest.raises(ValidationError, match=retired.rsplit('.', 1)[-1]):
        PlanChapters.model_validate(payload)


def test_source_size_table_reports_atoms_not_source_event_units():
    table = source_chars_table(_blueprints(), book_total=True)
    assert table and '原子事实 3 条' in table and '全书可定位源区间合计' in table
    assert '源事件' not in table and '分组' not in table


def test_clean_compiled_plan_closes_all_new_scene_hard_rules():
    findings = _findings(_compiled())
    for rule in (
        'one_to_one', 'fact_ref', 'fact_coverage',
        'causal_closure', 'state_closure', 'material_coverage', 'scene_transition',
        'goal_landing', 'budget_int', 'budget_band', 'plan_book_band', 'style_words',
    ):
        assert findings[rule] == [], f'{rule}: {findings[rule]}'


def test_fact_coverage_and_state_closure_fail_independently():
    """两条判据互不搭便车：覆盖坏了，可判的状态边界也要独立红。

    ⚠ 拿来破坏状态的**必须是一条可判边界**——章首 entry 是章级契约，与章内
    是否回溯正交。拿 `earlier` 支路的 entry 去破坏，那是判据 21 说的假红：
    产品明说那种边界机器判不了。
    """
    plan = _compiled()
    plan['output_chapters'][0]['scenes'][1]['land_fact_refs'].remove('f01_001')
    plan['output_chapters'][0]['scenes'][-1]['exit_state_refs'] = []
    findings = _findings(plan)
    assert findings['fact_coverage'] and findings['state_closure']
    assert '末场未闭合 ending_state' in findings['state_closure'][0].detail


def test_a_nonlinear_branch_entry_is_unverifiable_rather_than_a_violation():
    """`earlier` 支路不继承紧邻上一场的 exit——旧线性口径在这里恒假红。

    盘上的 `transition` 已经如实标了回溯，判据却按披露数组读，于是一次整书
    计划被判掉。现在它落 `unver` 并带扫描分母，逐项证据留给结构人审。
    """
    plan = _compiled()
    plan['output_chapters'][0]['scenes'][1]['entry_state_refs'] = []
    outcomes = {outcome.rule: outcome for outcome in checks.check(
        plan, blueprints=_blueprints(), main_text=_main())}
    state = outcomes['state_closure']
    assert list(state.findings) == []
    assert state.unver_why, '机器够不着的边界不许洗成绿'
    assert '场' in (state.scanned or ''), '绿灯/unver 都必须带扫描分母'

    reading = _findings(plan)['semantic_closure_reading']
    assert reading and 'state_unknown_findings' in reading[0].detail


def test_a_continue_scene_that_drops_the_previous_exit_state_is_still_hard():
    """放宽的只有非线性边界；`continue` 那一条一格没松。"""
    plan = _compiled()
    scene = plan['output_chapters'][0]['scenes'][1]
    scene['transition'] = 'continue'
    scene['entry_state_refs'] = []
    findings = _findings(plan)
    assert findings['state_closure'], 'continue 必须承接紧邻上一场退出状态'
    assert 'continue 未承接' in findings['state_closure'][0].detail


def test_reconstruction_metrics_are_always_reported_without_a_threshold():
    findings = _findings(_compiled())
    for rule in (
        'scene_budget_distribution', 'verbatim_share',
        'partition_change', 'disclosure_order', 'noncontiguous_regroup',
        'anchor_migration', 'structure_eligibility', 'structure_alignment',
        'semantic_closure_reading',
    ):
        assert findings[rule], f'{rule} 应常驻给读数，不应设自动放行阈值'


def _scenes(*rows: tuple[str, list[str], list[str], list[str]]) -> list[dict]:
    """`(transition, land, entry, exit)` 四元组的最小场序列。"""
    return [
        {'transition': transition, 'land_fact_refs': land,
         'entry_state_refs': entry, 'exit_state_refs': exit_refs}
        for transition, land, entry, exit_refs in rows
    ]


def _temporal(*rows):
    return checks.temporal_state_findings(
        _scenes(*rows), prefix='ch1', land_key='land_fact_refs',
        entry_key='entry_state_refs', exit_key='exit_state_refs')


def test_a_single_unambiguous_return_must_restore_the_anchor_state():
    """`earlier → return` 是唯一能配对的支路形状，那一条恢复检查仍是 HARD。"""
    bad, transition_bad, unknown = _temporal(
        ('start', ['a'], [], ['a']),
        ('earlier', ['b'], [], ['b']),
        ('return', ['c'], [], ['c']),
    )
    assert transition_bad == []
    assert any('return 未恢复支路前锚点状态' in row for row in bad)
    assert unknown, '支路入口本身仍然是人审读数'

    ok_bad, _t, _u = _temporal(
        ('start', ['a'], [], ['a']),
        ('earlier', ['b'], [], ['b']),
        ('return', ['c'], ['a'], ['c']),
    )
    assert ok_bad == []


def test_two_branch_openers_make_the_pairing_unjudgeable_instead_of_inventing_lifo():
    """当前 schema 没有 branch id/depth ⇒ 第二个 opener 之后只能报不可判。"""
    bad, _transition_bad, unknown = _temporal(
        ('start', ['a'], [], ['a']),
        ('earlier', ['b'], [], ['b']),
        ('parallel', ['c'], [], ['c']),
        ('return', ['d'], [], ['d']),
    )
    assert bad == [], '配不出唯一锚点时不许硬判违约'
    assert any('没有唯一可证明的本章支路锚点' in row for row in unknown)


def test_an_unclosed_branch_at_chapter_end_is_reported_not_swallowed():
    _bad, _transition_bad, unknown = _temporal(
        ('start', ['a'], [], ['a']),
        ('earlier', ['b'], [], ['b']),
    )
    assert any('章末仍有未显式 return' in row for row in unknown)


def test_transition_syntax_stays_hard_in_both_directions():
    _bad, transition_bad, _unknown = _temporal(('continue', ['a'], [], ['a']))
    assert any('首场 transition=continue' in row for row in transition_bad)

    _bad, transition_bad, _unknown = _temporal(
        ('start', ['a'], [], ['a']), ('start', ['b'], ['a'], ['b']))
    assert any('非首场不得 start' in row for row in transition_bad)

    _bad, transition_bad, _unknown = _temporal(
        ('start', ['a'], [], ['a']), ('teleport', ['b'], ['a'], ['b']))
    assert any('未知 transition=teleport' in row for row in transition_bad)


def test_the_response_prefixed_transitions_use_the_very_same_ruler():
    """A 相拿 `X_` 前缀，正式计划拿产物取值——两侧必须判出同一个结论。"""
    prefixed = checks.temporal_state_findings(
        _scenes(('X_START', ['a'], [], ['a']), ('X_CONTINUE', ['b'], [], ['b'])),
        prefix='ch1', land_key='land_fact_refs',
        entry_key='entry_state_refs', exit_key='exit_state_refs')
    plain = _temporal(('start', ['a'], [], ['a']), ('continue', ['b'], [], ['b']))
    assert prefixed == plain
    assert any('continue 未承接' in row for row in plain[0])


def test_a_causal_endpoint_that_enters_as_context_still_counts_as_landed():
    """端点落在 context 不是「因果边漂移」——旧口径只看 land，那是判据够不着。"""
    plan = _compiled()
    scenes = plan['output_chapters'][0]['scenes']
    # f01_003 改由第二场以 context 身份引用；它仍在本章的披露面里。
    positions = checks.causal_scene_positions(
        scenes, land_key='land_fact_refs', context_key='context_fact_refs')
    assert positions['f01_002'] == [0] and positions['f01_003'] == [1]

    moved = [
        {**scenes[0], 'context_fact_refs': ['f01_003']},
        {**scenes[1], 'land_fact_refs': ['f01_001', 'f01_003']},
    ]
    missing, reversed_order = checks.causal_scene_findings(
        [{'before': 'f01_002', 'after': 'f01_003'}],
        checks.causal_scene_positions(
            moved, land_key='land_fact_refs', context_key='context_fact_refs'))
    assert missing == [], 'context 身份的端点同样算入场'
    # 两端首次可见场都是 scene1 ⇒ 没有可判的逆序；同场披露不是倒叙。
    assert reversed_order == []


def test_a_causal_endpoint_in_no_scene_at_all_is_still_hard():
    """放宽的只有「算不算入场」；真的一场都没进，因果链就是断的。"""
    missing, reversed_order = checks.causal_scene_findings(
        [{'before': 'f01_002', 'after': 'f01_009'}],
        checks.causal_scene_positions(
            _compiled()['output_chapters'][0]['scenes'],
            land_key='land_fact_refs', context_key='context_fact_refs'))
    assert missing == ['f01_002>f01_009:未落入场景']
    assert reversed_order == []


def test_reverse_disclosure_uses_the_first_visible_scene_not_the_last():
    """`context` 可以在多场复用；只留最后一个会让先后取决于数组尾部。"""
    scenes = [
        {'land_fact_refs': ['b'], 'context_fact_refs': []},
        {'land_fact_refs': ['a'], 'context_fact_refs': ['b']},
    ]
    positions = checks.causal_scene_positions(
        scenes, land_key='land_fact_refs', context_key='context_fact_refs')
    assert positions['b'] == [0, 1]
    _missing, reversed_order = checks.causal_scene_findings(
        [{'before': 'a', 'after': 'b'}], positions)
    assert reversed_order == ['a>b'], '按首次可见场，b 先于 a'
