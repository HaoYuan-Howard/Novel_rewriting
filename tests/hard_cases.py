#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4/6/7 每条 HARD 判据的最小会红样本登记表（VAL-02）。

每个 ``build`` 都调用真实判据入口。Stage4 的样本以生产的
``identity_plan`` 为绿底座，再只破坏一条原子事实或场景契约；这里不保留退役分组
算法的测试替身。
"""
from __future__ import annotations

import copy
from typing import Callable, NamedTuple


class Case(NamedTuple):
    stage: str
    build: Callable[[], list]


# ═══ Stage6 ══════════════════════════════════════════════════════════════════

def _hook(text: str, *, src_hook: str | None = None, characters: dict | None = None,
          given: str = '下发面：本书人物与秘密的材料。') -> list:
    from novel.stages.stage6 import checks, prompts
    return checks.hook_findings(
        text, mat=prompts.Material(blocks=(given,)), secrets=[], arc=None,
        characters=characters or {}, src_hook=src_hook, hook_mechanical=None,
        quote_problems=[])


_SRC_HOOK = '她推开门，看见满地的信纸，和一段没有寄出的往事，写满了整面墙壁。'


# ═══ Stage7 ══════════════════════════════════════════════════════════════════

def _rename(**over) -> list:
    from novel.stages.stage7 import checks
    kw: dict = dict(
        name_map={'characters': [], 'surname_map': {}, 'kept': []},
        subs={}, problems=[], applied={}, residual=[], baseline={}, registry={},
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members={}, missing=[], invalids=[],
        post_units=[], title_line='书名：略')
    kw.update(over)
    return checks.rename_findings(**kw)


def _via_build_subs(characters: list[dict], *, extra_subs: tuple = (),
                    owners: dict | None = None,
                    universe: frozenset = frozenset()) -> list:
    from novel.stages.stage7.measure import build_subs
    _subs, _kept, problems = build_subs(characters, extra_subs, owners, universe)
    return _rename(problems=problems)


def _c7(cid: str, old: str, new: str, aliases: tuple = ()) -> dict:
    return {'char_id': cid, 'renamed': True, 'old_canonical': old,
            'new_canonical': new,
            'aliases': [{'old': old_alias, 'new': new_alias}
                        for old_alias, new_alias in aliases]}


def _rn_txt_stale() -> list:
    from contracts.schema.report import finding
    from novel.stages.stage7 import checks
    required = [
        finding(rule, detail='当前契约要求的书级说明', evidence=[])
        for rule in ('rn_taste_unver', 'rn_coverage_unver', 'rn_title_unver')
    ]
    outcomes = checks.check(
        findings=required, scanned='0 条映射', baseline_line='0 字',
        chapter_scanned='0 章',
        txt_state=(False, '盘上的成书与产物记的指纹不符'),
        approved=False, stale=[], missing=[], hook_stale=False, identity=None,
        title_line='书名：略', body_count=0, upstream=('fresh', 'match', 6))
    return [finding_ for outcome in outcomes for finding_ in outcome.findings]


def _norm7(value: str) -> str:
    from novel.core.text import norm
    return norm(value)


# ═══ Stage4：原子事实契约 + 场景编译器 ════════════════════════════════════════

_TEXT4 = '甲收到入住邀请。乙在门口拒绝甲。甲决定留下。'


def _span4(fragment: str) -> dict:
    start = _TEXT4.index(fragment)
    return {'text': fragment, 'start': start, 'end': start + len(fragment),
            'normalized': fragment.rstrip('。'), 'recovery': 'exact'}


_MAIN4 = [{'chapter_ordinal': 1, 'text': _TEXT4}]
_BPS4 = {'blueprints': [{
    'chapter_ordinal': 1,
    'chapter_function': '建立入住冲突并完成主角入局',
    'fact_atoms': [
        {'fact_id': 'f01_001', 'claim': '甲收到入住邀请', 'kind': 'information',
         'obligation': 'land', 'source_beat_ref': 'b01_01',
         'source': _span4('甲收到入住邀请。'), 'participant_char_ids': ['char_001']},
        {'fact_id': 'f01_002', 'claim': '乙拒绝甲入住', 'kind': 'event',
         'obligation': 'land', 'source_beat_ref': 'b01_02',
         'source': _span4('乙在门口拒绝甲。'),
         'participant_char_ids': ['char_001', 'char_002']},
        {'fact_id': 'f01_003', 'claim': '甲决定留下', 'kind': 'character_state',
         'obligation': 'land', 'source_beat_ref': 'b01_03',
         'source': _span4('甲决定留下。'), 'participant_char_ids': ['char_001']},
    ],
    'causal_edges': [{'before': 'f01_002', 'after': 'f01_003'}],
    'entry_state_refs': [], 'ending_state_refs': ['f01_003'],
    'beats': [
        {'beat_id': 'b01_01', 'span': _span4('甲收到入住邀请。'),
         'time_layer': 'present', 'place': '门外',
         'present_char_ids': ['char_001'], 'hinges': []},
        {'beat_id': 'b01_02', 'span': _span4('乙在门口拒绝甲。'),
         'time_layer': 'present', 'place': '门口',
         'present_char_ids': ['char_001', 'char_002'],
         'hinges': [{'hinge_id': 'h01_001', 'kind': '要害台词',
                     'source_beat_ref': 'b01_02', 'source': _span4('拒绝甲')}]},
        {'beat_id': 'b01_03', 'span': _span4('甲决定留下。'),
         'time_layer': 'present', 'place': '门口',
         'present_char_ids': ['char_001'], 'hinges': []},
    ],
    'narration': [],
}]}


def _clean_plan4() -> dict:
    from novel.stages.stage4.measure import identity_plan
    return identity_plan(_BPS4, _MAIN4)


def _ctx4(plan: dict) -> dict:
    """由正式 plan 投影一份判据所需的最小 writer_context 绿底座。"""
    from contracts.invariants import HINGE_GROUPS
    from novel.stages.stage4.beats import beat_index, verbatim_pieces

    bp = _BPS4['blueprints'][0]
    atoms = {row['fact_id']: row['claim'] for row in bp['fact_atoms']}
    bidx = beat_index(_BPS4, _MAIN4)
    hinges = {hinge['hinge_id']: hinge for row in bidx.values()
              for hinge in row.get('hinges') or []}
    scenes = []
    for planned in plan['output_chapters'][0].get('scenes') or []:
        materials = {field: [] for field in dict.fromkeys(HINGE_GROUPS.values())}
        for ref in planned.get('material_refs') or []:
            pieces, _unknown = verbatim_pieces([hinges[ref]])
            for field, values in pieces.items():
                materials[field].extend(values)
        scenes.append({
            'slot': planned.get('slot'),
            'required_facts': [atoms[ref] for ref in planned.get('land_fact_refs') or []],
            'context_facts': [atoms[ref]
                              for ref in planned.get('context_fact_refs') or []],
            'stages': [{'time_layer': bidx[ref].get('time_layer'),
                        'place': bidx[ref].get('place'),
                        'present': []}
                       for ref in planned.get('stage_refs') or []],
            **materials,
        })
    return {'chapters': [{
        'output_ordinal': 1, 'from_source_ordinals': [1], 'scenes': scenes,
        'characters': [], 'narration': [], 'facts': {'secrets': []},
    }]}


def _phase_links4(*, mismatch: bool = False) -> tuple:
    from contracts.artifacts import STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING
    from novel.stages.stage4.phase_binding import PhaseArtifactLink

    expected = 'a' * 64
    return (
        PhaseArtifactLink(
            provenance_field='blind_candidate_sha256',
            key=STAGE4_BLIND_CANDIDATE,
            expected_sha256=expected,
            now_sha256=('b' * 64 if mismatch else expected),
            state=('mismatch' if mismatch else 'match'),
        ),
        PhaseArtifactLink(
            provenance_field='evidence_binding_sha256',
            key=STAGE4_EVIDENCE_BINDING,
            expected_sha256=expected,
            now_sha256=expected,
            state='match',
        ),
    )


def _find4(plan: dict, *, ctx: dict | None = None,
           ctx_link: tuple[str, str] = ('ok', 'same plan'),
           phase_links: tuple | None = None) -> list:
    from novel.stages.stage4 import checks
    outcomes = checks.check(
        plan, blueprints=_BPS4, main_text=_MAIN4,
        ctx=_ctx4(plan) if ctx is None else ctx, ctx_link=ctx_link,
        phase_links=_phase_links4() if phase_links is None else phase_links)
    return [finding_ for outcome in outcomes for finding_ in outcome.findings]


def _bad_plan4(rule: str) -> list:
    plan = copy.deepcopy(_clean_plan4())
    clean_ctx = _ctx4(plan)
    chapter = plan['output_chapters'][0]
    scenes = chapter['scenes']
    if rule == 'one_to_one':
        chapter['from_source_ordinals'] = [2]
    elif rule == 'ordinal_seq':
        chapter['output_ordinal'] = 2
    elif rule == 'empty_chapter':
        chapter['scenes'] = []
    elif rule == 'fact_ref':
        scenes[0]['context_fact_refs'] = ['f01_999']
    elif rule == 'fact_coverage':
        scenes[0]['land_fact_refs'] = []
    elif rule == 'causal_closure':
        scenes[-1]['land_fact_refs'] = [
            ref for ref in scenes[-1]['land_fact_refs'] if ref != 'f01_003']
    elif rule == 'state_closure':
        scenes[0]['entry_state_refs'] = []
        scenes[-1]['exit_state_refs'] = []
    elif rule == 'material_coverage':
        for scene in scenes:
            scene['material_refs'] = []
    elif rule == 'scene_transition':
        scenes[0]['transition'] = 'continue'
    elif rule == 'goal_landing':
        chapter['open_hook_goal'] = {
            'intent': 'open',
            'fact_refs': [{'kind': 'fact_atom', 'ref': 'f01_002'}],
        }
    elif rule == 'budget_int':
        scenes[0]['budget_chars'] = 0
    elif rule == 'budget_band':
        for scene in scenes:
            scene['budget_chars'] = 1
    elif rule == 'plan_book_band':
        base = sum(scene['budget_chars'] for scene in scenes)
        scenes[-1]['budget_chars'] += round(base * 1.15) - base
    elif rule == 'style_words':
        chapter['chapter_rewrite_brief'] = '用张力强化整章'
    else:
        raise AssertionError(rule)
    return _find4(plan, ctx=clean_ctx)


def _bad_context4(rule: str) -> list:
    plan = _clean_plan4()
    ctx = _ctx4(plan)
    chapter = ctx['chapters'][0]
    if rule == 'ctx_facts_exact':
        chapter['scenes'][0]['required_facts'] = ['被中间层改写过的事实']
    elif rule == 'ctx_name_attested':
        chapter['characters'] = [
            {'char_id': 'char_999', 'display_name': 'char_999', 'role': '未知'}]
    elif rule == 'ctx_stage_explicit':
        chapter['scenes'][0]['stages'][0]['place'] = '错误地点'
    elif rule == 'ctx_hinge_delivered':
        for scene in chapter['scenes']:
            for field in ('verbatim', 'keep_value', 'must_appear', 'must_hold'):
                scene[field] = []
    else:
        raise AssertionError(rule)
    return _find4(plan, ctx=ctx)


# ═══ 登记表 ═══════════════════════════════════════════════════════════════════

CASES: dict[str, Case] = {
    'hook_empty': Case('stage6', lambda: _hook('短。')),
    'hook_truncated': Case('stage6', lambda: _hook(
        '这条文案写到一半没有任何终止标点就停在这里像被拦腰截断')),
    'hook_reuses_source': Case('stage6', lambda: _hook(_SRC_HOOK, src_hook=_SRC_HOOK)),
    'hook_invented_name': Case('stage6', lambda: _hook(
        '沈姑娘站在长街尽头吹了整夜的风，等一个不会回来的人回头。',
        characters={'c1': {'display_name': '沈凌', 'name_distinctiveness': 'high'}})),
    'hook_para_sep': Case('stage6', lambda: _hook(
        '第一段落的文字要足够长一点。\n第二段落的文字也足够长一点。',
        src_hook='第一段。\n\n第二段。')),

    'rn_llm_shape': Case('stage7', lambda: _rename(registry={'c1': {}})),
    'rn_map_coverage': Case('stage7', lambda: _via_build_subs(
        [], universe=frozenset({'狗剩'}))),
    'rn_new_collide': Case('stage7', lambda: _via_build_subs(
        [_c7('c1', '沈凌', '叶翎'), _c7('c2', '许晏', '叶翎')])),
    'rn_ambiguous': Case('stage7', lambda: _via_build_subs(
        [_c7('c1', '沈凌', '叶翎')], extra_subs=({'old': '沈凌', 'new': '岑越'},))),
    'rn_shared_surface': Case('stage7', lambda: _via_build_subs(
        [_c7('c1', '沈凌', '叶翎', aliases=(('书童', '小砚'),))],
        owners={_norm7('书童'): frozenset({'c1', 'c2'})})),
    'rn_surface_short': Case('stage7', lambda: _via_build_subs(
        [_c7('c1', '沈凌', '叶翎', aliases=(('姐', '妹'),))])),
    'rn_already_present': Case('stage7', lambda: _rename(
        subs={'沈凌': '叶翎'}, pre_target='叶翎早已出现在替换目标的成稿里。')),
    'rn_title_dropped': Case('stage7', lambda: _rename(subs={'李将军': '沈昭'})),
    'rn_semantic_marker_changed': Case('stage7', lambda: _rename(subs={'七郎': '六郎'})),
    'rn_kinship_touched': Case('stage7', lambda: _rename(subs={'姐姐': '妹妹'})),
    'rn_surname_split': Case('stage7', lambda: _rename(
        name_map={'characters': [], 'surname_map': {'沈': '叶', '许': '叶'}, 'kept': []})),
    'rn_overlap': Case('stage7', lambda: _via_build_subs(
        [], extra_subs=({'old': '阿禾姊', 'new': '李云'},
                        {'old': '姊姊', 'new': '妹妹'}))),
    'rn_residual_key': Case('stage7', lambda: _rename(residual_keys={'沈凌'})),
    'rn_book_incomplete': Case('stage7', lambda: _rename(missing=[2])),
    'rn_txt_stale': Case('stage7', _rn_txt_stale),

    **{rule: Case('stage4', lambda rule=rule: _bad_plan4(rule)) for rule in (
        'one_to_one', 'ordinal_seq', 'empty_chapter',
        'fact_ref', 'fact_coverage', 'causal_closure', 'state_closure',
        'material_coverage', 'scene_transition', 'goal_landing', 'budget_int',
        'budget_band', 'plan_book_band', 'style_words',
    )},
    'context_link': Case('stage4', lambda: _find4(
        _clean_plan4(), ctx_link=('stale', '上下文照的是另一版计划'))),
    'phase_artifact_link': Case('stage4', lambda: _find4(
        _clean_plan4(), phase_links=_phase_links4(mismatch=True))),
    **{rule: Case('stage4', lambda rule=rule: _bad_context4(rule)) for rule in (
        'ctx_facts_exact', 'ctx_name_attested', 'ctx_stage_explicit',
        'ctx_hinge_delivered',
    )},
}
