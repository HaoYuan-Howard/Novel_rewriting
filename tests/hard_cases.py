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


# ⚠⚠ 这里曾有 `_hook_semantic_contradiction()`——它靠猴补 `semantic._calibration_status`
#     返回 `(True, '')` 再手搓一个 `automated=True, verdict='FAIL'` 的假 decision，
#     才造得出 `contradiction` 状态。自动放行门下线后那两件东西都不存在了，而该判据
#     已由 HARD 降为 `unver`（见 `contracts/judges/stage6.py`），因此不再需要反例。
#     ⚠ 如果哪天它被改回 HARD，`tests/test_hard_counterexamples.py` 会当场报缺反例——
#     **那时不要再靠猴补造样本**：造不出来正说明那条路在产品里已经不可达。


_SRC_HOOK = '她推开门，看见满地的信纸，和一段没有寄出的往事，写满了整面墙壁。'


# ═══ stage7 ═══════════════════════════════════════════════════════════════════

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
        txt_state=(False, '盘上的成书与产物记的指纹不符——它被人动过或是旧的'),
        approved=False, stale=[], missing=[], hook_stale=False, identity=None,
        title_line='书名：略', body_count=0,
        # ⚠ 本反例针对的是 `rn_txt_stale`，来源那一格给 `fresh` 是为了**不让它也红**：
        #   一个反例只许红它点名的那一条（本文件整份的判准）。
        upstream=('fresh', 'match', 6))
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


#: `_BPS4` 的 beat 都不带保真材料 ⇒ `ctx_hinge_delivered` 在那上面走 `unver`（没有分母）。
#: 这一份给每拍挂一条**stage2 产物形态**的 hinge，用来造出「有材料却没下发」那个红。
#: ⚠ **形态必须是 `{'kind','source':{...}}`**，不是 `{'kind','text'}`——后者恰恰是
#: 那次事故里的中间形态，拿它当反例等于把断掉的那条路重新写进测试。
_BPS4_HINGED = {'blueprints': [
    {**bp, 'beats': [{**be, 'hinges': [{
        'kind': '要害台词',
        'source': {'text': '一句要害台词。', 'start': 0, 'end': 7,
                   'normalized': '一句要害台词', 'recovery': 'exact'}}]}
        for be in bp['beats']]}
    for bp in _BPS4['blueprints']]}


#: `structure_units` 的判定有四支（`novel/stages/stage4/checks.py:187-195`）：
#: ①重复引用 ②组内有 beat 没出现 ③一个源单元被拆到两个写作单元 ④组内换序。
#: 上面那张表里的 `structure_units` 样本只走 ①，而 ③④ **在 `_BPS4` 上构造不出来**：
#: 那份蓝图的 beat 都不带 `span` ⇒ `beat_index` 给 `source_chars=None` ⇒ `group_beats`
#: 让每拍独立成组，而**单元素组永远连续、永远落在同一个写作单元里**。
#: ⇒ 这一份专供那两支：三拍同层、前两拍够碎会被吸收成一个源内容单元、第三拍够大保持独立。
_BPS4_UNIT = {'blueprints': [
    {'chapter_ordinal': 1, 'beats': [
        {'beat_id': 'b1_01', 'event': '事件一', 'time_layer': 'present'},
        {'beat_id': 'b1_02', 'event': '事件二', 'time_layer': 'present'},
        {'beat_id': 'b1_03', 'event': '事件三', 'time_layer': 'present'}]}]}


def _bidx4_unit() -> dict:
    """`_BPS4_UNIT` 的量纲表：让 b1_01+b1_02 合成一个源内容单元，b1_03 保持独立。

    ⚠ **底座走 `beat_index` 这个生产落点再改 `source_chars`**，不手搓整个字典：
    手搓一份就得把 `ord`/`hinges`/`time_layer`/`place`/`present_char_ids` 全抄一遍，
    而 `beat_index` 一旦多产一个键，抄的那份就悄悄地与生产分家
    （`tests/test_stage4_beats.py::test_beat_index_hands_verbatim_pieces_a_form_it_can_actually_read`
    记着这条缝断掉时是什么下场）。这里只按引用改一个字段。
    """
    from contracts.invariants import BEAT_MIN_CHARS
    from novel.stages.stage4.beats import beat_index

    idx = beat_index(_BPS4_UNIT, [_MAIN4[0]])
    for bid, n in (('b1_01', 40), ('b1_02', 40), ('b1_03', BEAT_MIN_CHARS)):
        idx[bid]['source_chars'] = n
    return idx


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


#: 四拍**全现场层**、各自独立成源内容单元的一章。两条新判据都需要它：
#: `structure_changed` 要「源单元 ≥ `PLAN_RECONSTRUCT_MIN_UNITS`」才判；
#: `transition_direction` 要「接缝两端都是 `present`」才判——`_BPS4` 的 `b1_02` 是 `recall`，
#: 拿它造靶子会让接缝落进「判不动」，而那与「查过没事」在断言上同形。
_BPS4_PRESENT = {'blueprints': [
    {'chapter_ordinal': 1, 'beats': [
        {'beat_id': f'b1_0{i}', 'event': f'事件{i}', 'time_layer': 'present'}
        for i in range(1, 5)]}]}


def _bidx4_present() -> dict:
    """`_BPS4_PRESENT` 的量纲表：四拍各 100 字，**都够大 ⇒ 各自独立成源内容单元**。

    `structure_distance` 的详略两项（拍级 `预算/源` 离散度、`summary` 承载比）需要
    真的算得出 span 的拍——`_BPS4_PRESENT` 的 beat 不带 `span` ⇒ `source_chars=None`
    ⇒ 那一维会**正确地**报「判不动」。⚠ 底座同样走 `beat_index` 再按引用改一个字段，
    理由与 `_bidx4_unit` 那条一字不差。
    """
    from novel.stages.stage4.beats import beat_index

    idx = beat_index(_BPS4_PRESENT, [_MAIN4[0]])
    for bid in idx:
        idx[bid]['source_chars'] = 100
    return idx


def _bad_transition4() -> list:
    chapters = _GREEN4()
    chapters[0]['segments'][0]['transition'] = 'continue'
    return _plan4(chapters)


def _identity_structure4() -> list:
    """`structure_changed` 的反例**来自生产落点**：`identity_plan()` 产的就是恒等基线。

    ⚠⚠ 手搓一份「一单元一段 · 全 `scene` · 按源序」的计划，等于把恒等基线的定义**抄第二遍**，
    而这条 HARD 判的正是「与那个定义逐格相同」。定义一改，抄的那份就悄悄不再是靶子，
    这条反例会静静地绿——那是 `LESSONS §21` 第一形状（守卫重拼了生产的实参）。
    `--identity` 是 `FREE_ENTRIES` 里的现役 0 成本节点，它每跑一次就产一份这样的计划。

    ⚠ 它同时是「新口径没被换成恒绿」的证明：换口径后 `structure_changed` 判的是三根轴
    一根都没动，而恒等基线正是那个唯一的样本。
    """
    from novel.stages.stage4.measure import IDENTITY_GOAL, identity_plan

    plan = identity_plan(_BPS4_PRESENT, [_MAIN4[0]])
    return _plan4(plan['output_chapters'], strategy=IDENTITY_GOAL,
                  blueprints=_BPS4_PRESENT, main_text=[_MAIN4[0]])


def _bad_direction4() -> list:
    """`transition_direction`：接缝两端都是现场层、源位置在**前进**，标签却宣称 `earlier`。

    摆法 `[b1_01,b1_02] [b1_03,b1_04]`：接缝是 `b1_02`(源位置 2) → `b1_03`(源位置 3)，
    往后走，而第二段标 `earlier`（回到更早）⇒ 闭卷写手会照着写一句方向反了的过渡。
    ⚠ 分组与源序不同（两两合并）⇒ `structure_changed` 不会跟着响，靶子是干净的。
    """
    chapters = [_ch4(1, [_b4('b1_01', 'b1_02'), _b4('b1_03', 'b1_04')])]
    chapters[0]['segments'][1]['transition'] = 'earlier'
    return _plan4(chapters, blueprints=_BPS4_PRESENT, main_text=[_MAIN4[0]])


def _bad_goal_landing4() -> list:
    chapters = _GREEN4()
    chapters[0]['open_hook_goal']['fact_refs'][0]['ref'] = 'b1_02'
    return _plan4(chapters)


def _book_band_over4() -> list:
    """全书顶出总量带，而**每一章都留在逐章带之内**——这条 HARD 存在的全部理由。

    `_MAIN4` 两章各 280 源字 ⇒ 逐章带 0.80~1.20 = 224~336、书级带 0.90~1.10 = 504~616。
    每章给 330（1.179×，**章带内**）⇒ 全书 660 > 616。
    ⚠⚠ 靶子必须是这个形状：随便造一个「某章预算离谱」的样本，`budget_band` 会先红，
    于是这条反例证明不了新判据拦住了旧判据拦不住的东西——而那正是它被加进来的理由。
    配套的正向断言在 `test_stage4.py::test_a_book_that_overshoots_...`。
    ⚠ 空蓝图 ⇒ 没有 span 覆盖账（`cov=(0,0)` 过闸）、源字数回退整章长度，
    与 `budget_band` 那条反例走同一条路：两条判据共用一个基准，靶子也共用一种造法。
    """
    return _plan4([_ch4(1, [_b4(budget=110), _b4(budget=110), _b4(budget=110)]),
                   _ch4(2, [_b4(budget=110), _b4(budget=110), _b4(budget=110)])],
                  blueprints={'blueprints': []})


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

    # ── stage4（20 HARD）──────────────────────────────────────────────────
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
    'structure_changed': Case('stage4', _identity_structure4),
    'transition_direction': Case('stage4', _bad_direction4),
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
    'plan_book_band': Case('stage4', _book_band_over4),
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
    # stage2 抽到了保真材料，下发面上一条都没有 —— 这正是真事故在产物上的形状
    # （`beat_index` → `verbatim_pieces` 形态分家，五本书 898 条 hinge 全部没能下发）。
    'ctx_hinge_delivered': Case('stage4', lambda: _plan4(
        _GREEN4(), blueprints=_BPS4_HINGED,
        ctx={'chapters': [_ctx_ch(segments=[{'slot': 's01', 'events': ['事件一']}])]},
        global_g={'entities': {'characters': {}}})),
}


def _norm7(s: str) -> str:
    from novel.core.text import norm
    return norm(s)
