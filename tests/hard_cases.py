#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4/6/7 每条 HARD 判据的最小会红样本登记表（VAL-02）。

每个 ``build`` 都调用真实判据入口。Stage4 的样本以生产的
``identity_plan`` 为绿底座，再只破坏一个完整节点、呈现端点或场景契约；这里不保留退役分组
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
        text, mat=prompts.Material(blocks=(given,)), secrets=[],
        characters=characters or {}, src_hook=src_hook, hook_mechanical=None,
        quote_problems=[])


_SRC_HOOK = '她推开门，看见满地的信纸，和一段没有寄出的往事，写满了整面墙壁。'


# ═══ Stage7 ══════════════════════════════════════════════════════════════════

def _rename(**over) -> list:
    from novel.stages.stage7 import checks
    kw: dict = dict(
        name_map={'characters': [], 'surname_map': {}, 'kept': []},
        subs={}, problems=[], applied={}, residual=[], baseline={},
        dispatched_characters={},
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


# ═══ Stage4：完整剧情与呈现契约 ═══════════════════════════════════════════════
from tests.plot_fixtures import (
    TEXT as _TEXT4, MAIN as _MAIN4, GLOBAL as _GLOBAL4, REGISTRY as _CHARACTERS4,
    blueprint as _blueprints4, links as _links4, plan as _clean_plan4, context as _ctx4,
)
_BPS4 = _blueprints4()

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
           phase_links: tuple | None = None, blueprints: dict | None = None) -> list:
    from novel.stages.stage4 import checks
    # 坏计划不能先经 Context 编译器拒绝，必须让目标生产判据看到反例。
    expected = _ctx4(_clean_plan4())
    outcomes = checks.check(
        plan, blueprints=_BPS4 if blueprints is None else blueprints, main_text=_MAIN4,
        global_g=_GLOBAL4, characters=_CHARACTERS4, knowledge_links=_links4(),
        ctx=expected if ctx is None else ctx, expected_ctx=expected, ctx_link=ctx_link,
        phase_links=_phase_links4() if phase_links is None else phase_links)
    return [finding_ for outcome in outcomes for finding_ in outcome.findings]


def _bad_plan4(rule: str) -> list:
    plan = copy.deepcopy(_clean_plan4())
    chapter = plan['output_chapters'][0]
    scenes = chapter['scenes']
    if rule == 'one_to_one':
        chapter['from_source_ordinals'] = [2]
    elif rule == 'ordinal_seq':
        chapter['output_ordinal'] = 2
    elif rule == 'empty_chapter':
        chapter['scenes'] = []
    elif rule == 'placement_ref':
        chapter['beat_placements'].pop()
    elif rule == 'scene_transition':
        scenes[0]['transition'] = 'continue'
    elif rule == 'goal_landing':
        chapter['open_hook_goal']['anchor_scene_ref'] = 's02'
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
    return _find4(plan)


def _bad_context4(rule: str) -> list:
    plan = _clean_plan4()
    ctx = _ctx4(plan)
    chapter = ctx['chapters'][0]
    if rule == 'ctx_plot_exact':
        chapter['beats'][0]['content'] = '被中间层改写过的剧情'
    elif rule == 'ctx_name_attested':
        chapter['characters'][0]['display_name'] = '虚构姓名'
    elif rule == 'ctx_permission_exact':
        chapter['scenes'][-1]['state_events'] = []
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

    'rn_llm_shape': Case('stage7', lambda: _rename(
        dispatched_characters={'c1': {'canonical': '沈凌', 'aliases': []}})),
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
        'placement_ref', 'scene_transition', 'goal_landing', 'budget_int',
        'budget_band', 'plan_book_band', 'style_words',
    )},
    'context_link': Case('stage4', lambda: _find4(
        _clean_plan4(), ctx_link=('stale', '上下文照的是另一版计划'))),
    'phase_artifact_link': Case('stage4', lambda: _find4(
        _clean_plan4(), phase_links=_phase_links4(mismatch=True))),
    **{rule: Case('stage4', lambda rule=rule: _bad_context4(rule)) for rule in (
        'ctx_plot_exact', 'ctx_name_attested', 'ctx_permission_exact',
    )},
}
