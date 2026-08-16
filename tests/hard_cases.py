#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hard_cases.py —— stage4/6/7 每条 HARD 判据的**最小会红样本**登记表（VAL-02）。

【纪律】
  · `build()` 必须调**真实判据入口**产 `Finding`（stage4 `checks.check` ·
    stage6 `checks.hook_findings`/`semantic.outcomes` · stage7 `build_subs`→
    `rename_findings`/`checks.check`），不许手搓 `finding(...)` 充数——
    手搓的样本证明不了判据函数真的会拦。
  · **最小性靠自律不靠断言**：部分判据共享前置，强断言「不溅射其他 HARD」会把
    构造成本翻倍；每个样本只保证「目标判据必红」。
  · 新增一条 HARD 而不登记反例，守卫测试当场红。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, NamedTuple


class Case(NamedTuple):
    stage: str
    build: Callable[[], list]          # -> list[Finding]，目标判据必在其中


# ═══ stage6 ═══════════════════════════════════════════════════════════════════

def _hook(text: str, *, src_hook: str | None = None, characters: dict | None = None,
          given: str = '下发面：本书人物与秘密的材料。') -> list:
    from novel.stages.stage6 import checks, prompts
    return checks.hook_findings(
        text, mat=prompts.Material(blocks=(given,)), secrets=[], arc=None,
        characters=characters or {}, src_hook=src_hook, style_hook=None,
        quote_problems=[])


def _hook_semantic_contradiction() -> list:
    """经 `semantic.outcomes` 的真实裁决物化路径（latest_semantic 临时替身，跑完复原）。"""
    from novel.stages.stage6 import semantic

    text = '她嫁进将军府那晚，就发现枕头下压着一封没有署名的信。'
    from contracts.schema.stage6 import HookCandidateReview
    art = SimpleNamespace(
        candidates=[SimpleNamespace(approach='source_architecture', text=text)],
        approved=None)
    review = HookCandidateReview.model_validate({
        'label': 'source_architecture',
        'coverage': 'complete',
        'uncertainty_reason': None,
        'contradictions': [{
            'assertion': '她与将军成婚',
            'evidence': '她嫁进将军府',
            'reason': '全书设定中两人并非婚姻关系',
        }],
        'quality': {
            'read_on_desire': 8, 'body_voice_match': 8,
            'concrete_scene_and_specificity': 8,
            'curiosity_and_payoff_promise': 8, 'hook_craft': 8,
            'synopsis_penalty': 1,
        },
        'concise_reason': '场面具体，但关系写反。',
        'text_sha1': semantic.text_sha1(text),
    })
    subject = semantic.text_sha256(text)
    decision = SimpleNamespace(
        subject_key=subject,
        automated=True,
        verdict='FAIL',
        reason='两名盲评一致判定关系矛盾',
    )
    fake = SimpleNamespace(
        reviews=[review],
        decisions=[decision],
        ranking=['source_architecture'],
    )
    store = SimpleNamespace(freshness=lambda _rel: SimpleNamespace(state='fresh'))
    original = semantic.latest_semantic
    original_status = semantic._calibration_status
    original_seqs = semantic.semantic_seqs
    semantic.latest_semantic = lambda _s: (1, fake)
    semantic._calibration_status = lambda _artifact: (True, '')
    semantic.semantic_seqs = lambda _store: [1]
    try:
        outcomes = semantic.outcomes(store, art)
    finally:
        semantic.latest_semantic = original
        semantic._calibration_status = original_status
        semantic.semantic_seqs = original_seqs
    return [f for o in outcomes for f in o.findings]


_SRC_HOOK = '她推开门，看见满地的信纸，和一段没有寄出的往事，写满了整面墙壁。'


# ═══ stage7 ═══════════════════════════════════════════════════════════════════

def _rename(**over) -> list:
    from novel.stages.stage7 import checks
    kw: dict = dict(
        name_map={'characters': [], 'surname_map': {}, 'kept': []},
        subs={}, problems=[], applied={}, residual=[], baseline={}, registry={},
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members={}, dup=[], missing=[], invalids=[],
        post_units=[], title_line='书名：略')
    kw.update(over)
    return checks.rename_findings(**kw)


def _via_build_subs(characters: list[dict], *, extra_subs: tuple = (),
                    owners: dict | None = None, universe: frozenset = frozenset()) -> list:
    """真实两段流水线：`build_subs` 产 problems → `rename_findings` 物化成 Finding。"""
    from novel.stages.stage7.measure import build_subs
    _subs, _kept, problems = build_subs(characters, extra_subs, owners, universe)
    return _rename(problems=problems)


def _c7(cid: str, old: str, new: str, aliases: tuple = ()) -> dict:
    return {'char_id': cid, 'renamed': True, 'old_canonical': old,
            'new_canonical': new, 'aliases': [{'old': o, 'new': n} for o, n in aliases]}


def _rn_txt_stale() -> list:
    from contracts.schema.report import finding
    from novel.stages.stage7 import checks
    required = [
        finding(rule, detail='当前契约要求的书级说明', evidence=[])
        for rule in ('rn_taste_unver', 'rn_coverage_unver', 'rn_title_unver')
    ]
    outcomes = checks.check(
        findings=required, scanned='0 条映射', baseline_line='0 字', chapter_scanned='0 章',
        txt_state=(False, '盘上的 book.txt 与产物记的指纹不符——它被人动过或是旧的'),
        approved=False, stale=[], missing=[], hook_stale=False, identity=None,
        title_line='书名：略', body_count=0)
    return [f for o in outcomes for f in o.findings]


# ═══ stage4 ═══════════════════════════════════════════════════════════════════

_MAIN4 = [{'chapter_ordinal': 1, 'text': '第一章的正文。' * 40},
          {'chapter_ordinal': 2, 'text': '第二章的正文。' * 40}]
_BPS4 = {'blueprints': [
    {'chapter_ordinal': 1, 'beats': [
        {'beat_id': 'b1_01', 'event': '事件一', 'time_layer': 'present'},
        {'beat_id': 'b1_02', 'event': '事件二', 'time_layer': 'recall'},
        {'beat_id': 'b1_03', 'event': '事件三', 'time_layer': 'present'}]},
    {'chapter_ordinal': 2, 'beats': [
        {'beat_id': 'b2_01', 'event': '事件四', 'time_layer': 'present'}]}]}


def _b4(*ids: str, budget: int = 100) -> dict:
    return {'budget_chars': budget, 'from_beats': list(ids)}


def _ch4(oo: int, beats: list[dict], srcs: list[int] | None = None,
          goal: str = '讲清这一章发生了什么。') -> dict:
    segments = [{**beat, 'slot': f's{i:02d}',
                 'narrative_role': 'setup' if i == 1 else 'bridge',
                 'delivery': 'scene', 'transition': 'start' if i == 1 else 'continue'}
                for i, beat in enumerate(beats, 1)]
    first_ref = ((beats[0].get('from_beats') or [None])[0] if beats else None)
    last_ref = ((beats[-1].get('from_beats') or [None])[-1] if beats else None)
    goal_of = lambda intent, text, ref: {
        'intent': intent,
        'fact_refs': [{'kind': 'event', 'ref': ref, 'chapter': oo}]
        if ref else [{'kind': 'chapter_boundary', 'ref': f'chapter:{oo}', 'chapter': oo}],
        'rationale': text,
    }
    return {'output_ordinal': oo, 'from_source_ordinals': srcs if srcs is not None else [oo],
            'chapter_goal': goal_of('advance', goal, first_ref),
            'open_hook_goal': goal_of('open', '以日常开场。', first_ref),
            'end_hook_goal': goal_of('close', '以悬念收束。', last_ref), 'segments': segments}


_GREEN4 = lambda: [_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03')]),
                   _ch4(2, [_b4('b2_01')])]


def _bad_transition4() -> list:
    chapters = _GREEN4()
    chapters[0]['segments'][0]['transition'] = 'continue'
    return _plan4(chapters)


def _bad_goal_landing4() -> list:
    chapters = _GREEN4()
    chapters[0]['open_hook_goal']['fact_refs'][0]['ref'] = 'b1_02'
    return _plan4(chapters)


def _plan4(chapters: list[dict], *, strategy: str = '把故事原样讲完。',
           blueprints: dict | None = None, main_text: list | None = None, **kw) -> list:
    from novel.stages.stage4 import checks
    outcomes = checks.check({'reconstruction_strategy': strategy,
                             'output_chapters': chapters},
                            blueprints=blueprints if blueprints is not None else _BPS4,
                            main_text=main_text if main_text is not None else _MAIN4, **kw)
    return [f for o in outcomes for f in o.findings]


def _ctx4(chapters: list[dict], *, with_gg: bool = True) -> list:
    return _plan4(_GREEN4(), ctx={'chapters': chapters},
                  global_g={'entities': {'characters': {}}} if with_gg else None)


def _ctx_ch(oo: int = 1, **over) -> dict:
    base: dict = {'output_ordinal': oo, 'from_source_ordinals': [oo],
                  'segments': [], 'characters': {}, 'narration': []}
    base.update(over)
    return base


# ═══ 登记表 ═══════════════════════════════════════════════════════════════════

CASES: dict[str, Case] = {
    # ── stage6（5 机械 HARD + 1 语义 HARD）────────────────────────────────
    'hook_empty': Case('stage6', lambda: _hook('短。')),
    'hook_truncated': Case('stage6', lambda: _hook(
        '这条文案写到一半没有任何终止标点就停在这里像被拦腰截断')),
    'hook_reuses_source': Case('stage6', lambda: _hook(_SRC_HOOK, src_hook=_SRC_HOOK)),
    # ⚠ 内容字符要 ≥20（EMPTY_MIN），否则先触发 hook_empty 早退、其余判据一条不跑。
    'hook_invented_name': Case('stage6', lambda: _hook(
        '沈姑娘站在长街尽头吹了整夜的风，等一个不会回来的人回头。',
        characters={'c1': {'display_name': '沈凌', 'name_distinctiveness': 'high'}})),
    'hook_para_sep': Case('stage6', lambda: _hook(
        '第一段落的文字要足够长一点。\n第二段落的文字也足够长一点。',
        src_hook='第一段。\n\n第二段。')),
    'hook_semantic_contradiction': Case('stage6', _hook_semantic_contradiction),

    # ── stage7（15 HARD）──────────────────────────────────────────────────
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
    # ⚠ 后缀要真在 `NAME_TITLE` 词表里（嬷嬷/公子/…/将军/氏）——「许总」的「总」不在。
    'rn_title_dropped': Case('stage7', lambda: _rename(subs={'李将军': '沈昭'})),
    'rn_semantic_marker_changed': Case('stage7', lambda: _rename(subs={'七郎': '六郎'})),
    'rn_kinship_touched': Case('stage7', lambda: _rename(subs={'姐姐': '妹妹'})),
    'rn_surname_split': Case('stage7', lambda: _rename(
        name_map={'characters': [], 'surname_map': {'沈': '叶', '许': '叶'}, 'kept': []})),
    'rn_overlap': Case('stage7', lambda: _via_build_subs(
        [], extra_subs=({'old': '阿禾姊', 'new': '李云'}, {'old': '姊姊', 'new': '妹妹'}))),
    'rn_residual_key': Case('stage7', lambda: _rename(residual_keys={'沈凌'})),
    'rn_book_incomplete': Case('stage7', lambda: _rename(missing=[2])),
    'rn_txt_stale': Case('stage7', _rn_txt_stale),

    # ── stage4（17 HARD）──────────────────────────────────────────────────
    'one_to_one': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03'), _b4('b2_01')], srcs=[1, 2])])),
    'ordinal_seq': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03')]),
         _ch4(3, [_b4('b2_01')], srcs=[2])])),
    'empty_chapter': Case('stage4', lambda: _plan4(
        [_ch4(1, []), _ch4(2, [_b4('b2_01')])])),
    'beat_coverage': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b1_01')]), _ch4(2, [_b4('b2_01')])])),
    'beat_ref': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b9_99'), _b4('b1_01'), _b4('b1_02'), _b4('b1_03')]),
         _ch4(2, [_b4('b2_01')])])),
    'structure_changed': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03'), _b4('b1_04')])],
        blueprints={'blueprints': [{'chapter_ordinal': 1, 'beats': [
            {'beat_id': f'b1_0{i}', 'event': f'事件{i}', 'time_layer': 'present'}
            for i in range(1, 5)]}]}, main_text=[_MAIN4[0]])),
    'structure_units': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b1_01'), _b4('b1_01'), _b4('b1_02'), _b4('b1_03')]),
         _ch4(2, [_b4('b2_01')])])),
    'structure_transition': Case('stage4', _bad_transition4),
    'goal_landing': Case('stage4', _bad_goal_landing4),
    'budget_int': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b1_01', budget=0), _b4('b1_02'), _b4('b1_03')]),
         _ch4(2, [_b4('b2_01')])])),
    # 空蓝图 ⇒ 该章无 span 覆盖账（cov=(0,0) 过闸），源字数回退整章长度 ⇒ 预算带可判。
    'budget_band': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4(budget=10)])], blueprints={'blueprints': []},
        main_text=[_MAIN4[0]])),
    'context_link': Case('stage4', lambda: _plan4(
        _GREEN4(), ctx_link=('stale', '上下文照的是另一版计划'))),
    'style_words': Case('stage4', lambda: _plan4(
        [_ch4(1, [_b4('b1_01'), _b4('b1_02'), _b4('b1_03')], goal='把这一章的张力拉满。'),
         _ch4(2, [_b4('b2_01')])])),
    'ctx_events_verbatim': Case('stage4', lambda: _ctx4(
        [_ctx_ch(segments=[{'slot': 's01', 'events': ['被中间层转抄改写过的一句话']}])])),
    'ctx_name_attested': Case('stage4', lambda: _ctx4(
        [_ctx_ch(characters={'c1': {'display_name': '原文中不存在之名'}})])),
    'ctx_secret_gate': Case('stage4', lambda: _ctx4(
        [_ctx_ch(must_not_reveal=['s1'], facts={'secrets': [{'secret_id': 's1'}]})])),
    'ctx_layer_flat': Case('stage4', lambda: _ctx4(
        [_ctx_ch(segments=[{'slot': 's01', 'time_layer': 'present+recall', 'events': ['事件一']}])])),
}


def _norm7(s: str) -> str:
    from novel.core.text import norm
    return norm(s)
