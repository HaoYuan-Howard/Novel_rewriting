#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2.py —— Stage 2 当前判据。

⚠⚠ **本文件的主体是「盘上 0 命中的 HARD 各配一份会让它红的样本」**（2026-08-12 用户裁决）。
40 条 HARD 里盘上只有 **4 条**有真反例（`address_bare_pronoun` 1 本 · `role_leaks_fate` 2 本 ·
`hedge_words` 2 本 · `hinge_in_hole` 1 本）——**其余 36 条盘上 0 命中，那不是「干净」，
是那种失效模式在这三本上一次都没发生过**。一条从没见过反例的判据与一条不存在的判据，
在报告上长得一模一样。

⚠ **反例配得出来的前提是判据吃 `dict` 不吃模型**（`novel/stages/stage2/checks.py` 文件头）：
一旦跑在已过 Pydantic 的对象上，`role_missing` / `event_empty`
那一类在**结构上就构造不出反例**——`min_length=1` 会先把样本拒掉。
本文件因此全程操作 dict，一次都不 `model_validate`。

【干净底座是本文件的另一半】`test_the_clean_base_has_no_hard_hits` 钉住：
**底座上 40 条 HARD 一条都不命中**。没有它，下面 36 条「改一处就红」什么也证明不了
——一个本来就红的底座上，改什么都红。
"""
from __future__ import annotations

import copy
from typing import Any, Callable

import pytest

from contracts.rules import REGISTRY
from novel.core.spans import content_only
from novel.stages.stage2 import checks

# ─────────────────────────────────────────────────────────────────────────────
# 一、干净底座
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠ 三章是**下限不是随意**：`beats_coarse` 拿**本书自己的中位数**当基准，
#   两章时中位数就是那两个数的均值，`r > 3 × median` 在代数上无解
#   （2r > 3r' + 3r ⇒ −r > 3r'）。⇒ 想给它配反例，底座至少三章。
# ⚠ 十拍也是下限：`time_layer_no_present` 的前件是 `n >= 10`
#   （章数太少时全是回忆是可能的，那条不该报）。

CH1 = ('春风吹过老宅的院墙。李宝珠站在廊下看着落花。'
       '她想起了昨夜那封信。信上说父亲已经回城。她把信折好收进袖中。远处传来更夫的梆子声。')
CH2 = ('第二日清晨天色微亮。李宝珠推开院门往外走。'
       '街上已经有小贩在吆喝。她买了两个热腾腾的包子。回来时看见母亲在门口等她。'
       '其实母亲早就知道那封信的事。')
CH3 = ('午后的日头晒得人发懒。李宝珠把账册摊在桌上一页页翻。'
       '管家在门外回话说米价又涨了。她皱着眉让管家去问问别家。'
       '窗外的槐树落了一地碎叶。她忽然想起父亲从前也这样翻账。'
       '那时她还够不着桌子。母亲进来给她添了一盏茶。'
       '茶是新沏的还烫着。她端起来吹了吹没有喝。')


def _source(text: str, a: str, b: str | None = None) -> dict[str, Any]:
    start = text.index(a)
    end_probe = b or a
    end = text.index(end_probe, start) + len(end_probe)
    raw = text[start:end]
    return {'text': raw, 'start': start, 'end': end,
            'normalized': content_only(raw), 'recovery': 'exact'}


def _beat(bid: str, a: str, b: str, *, event: str, place: str,
          names: list[str], ids: list[str], hinges: list[dict] | None = None,
          layer: str = 'present') -> dict[str, Any]:
    chapter = {'01': CH1, '02': CH2, '03': CH3}[bid[1:3]]
    current_hinges = [
        {'kind': hinge['kind'],
         'source': _source(chapter, hinge['starts_with'], hinge['ends_with'])}
        for hinge in (hinges or [])
    ]
    return {'beat_id': bid, 'span': _source(chapter, a, b),
            'time_layer': layer, 'place': place,
            'present_names': list(names), 'present_char_ids': list(ids),
            'event': event, 'hinges': current_hinges}


def _narration(focalizer: str | None = '李宝珠', cid: str | None = 'char_001') -> list[dict]:
    return [{'grammatical_person': 'third', 'narrator_name': None,
             'focalizer_name': focalizer, 'narrator_char_id': None,
             'focalizer_char_id': cid, 'pronoun_surfaces': []}]


def main_text() -> list[dict[str, Any]]:
    return [{'section_id': f'sec_{i:04d}', 'chapter_ordinal': i,
             'source_chapter_label': str(i), 'text': t}
            for i, t in enumerate((CH1, CH2, CH3), start=1)]


def global_grounding() -> dict[str, Any]:
    return {
        'entities': {
            'characters': {
                'char_001': {
                    'canonical': '李宝珠',
                    'address': [{'surface': '李宝珠', 'by': 'narration',
                                 'context': 'reference', 'from_chapter': 1}],
                    'name_distinctiveness': 'high',
                    'role': '沈家的独女，替父亲掌管家中账目',
                    'fate': None,
                },
                'char_002': {
                    'canonical': '沈母',
                    'address': [{'surface': '母亲', 'by': 'char_001',
                                 'context': 'reference', 'from_chapter': 2}],
                    'name_distinctiveness': 'medium',
                    'role': '李宝珠的娘，管着老宅内院的日常',
                    'fate': None,
                },
            },
            'relations': [{'relation_id': 'rel_01', 'subject_id': 'char_001',
                           'object_id': 'char_002', 'predicate': '母女',
                           'valid_from_chapter': 1, 'valid_to_chapter': None}],
        },
        'canon': {
            'setting': {'时代背景': '架空古代江南的一座小城',
                        '世界观': '寻常人家的宅院生活，没有神怪，规矩由宗族与官府共同维持'},
            'world_rules': [{'rule_id': 'wr_01', 'kind': 'social_norm',
                             'versions': [{'effective_from_chapter': 1,
                                           'desc': '城中入夜有宵禁，更夫敲过梆子之后不许出门走动'}]}],
            'secrets': [{'secret_id': 'secret_01',
                         'fact': '父亲早已回城却一直不肯露面',
                         'character_known_by': ['char_001'],
                         'setup_chapters': [1],
                         'reader_reveal_events': [{'chapter': 2}]}],
        },
        'arc': {
            'conflict_stages': [{'stage': '家事初起', 'chapters': [1, 2, 3]}],
            'climax': '账册上的窟窿被当众翻出来的那一刻',
            'ending': '她替父亲把这笔烂账认了下来',
            'arc_tracks': [{'subject': 'char_001', 'dimension': '处境',
                            'change_type': 'development',
                            'trajectory': '从被瞒着到自己扛下这件事'}],
        },
        '_provenance': {
            'address_by_fallback': 0,
            'address_surface_rewrites': [],
            'address_from_chapter_raised': [],
            'canonical_distinguishers_stripped': [],
            'setup_chapters_pruned': [],
        },
    }


def blueprints() -> dict[str, Any]:
    return {
        'blueprints': [
            {'chapter_ordinal': 1, 'summary': '李宝珠在廊下看落花，想起昨夜那封说父亲回城的信',
             'narration': _narration(),
             'beats': [
                 _beat('b01_01', '春风吹过老宅', '看着落花。',
                       event='李宝珠站在廊下看落花', place='老宅廊下',
                       names=['李宝珠'], ids=['char_001'],
                       hinges=[{'kind': '物件', 'starts_with': '院墙',
                                'ends_with': '院墙', 'text': '院墙'}]),
                 _beat('b01_02', '她想起了昨夜', '梆子声。',
                       event='她读过信后把信收进袖中', place='老宅廊下',
                       names=['李宝珠'], ids=['char_001'],
                       hinges=[{'kind': '因果前提', 'starts_with': '信上说',
                                'ends_with': '已经回城。', 'text': '信上说父亲已经回城。'}]),
             ]},
            {'chapter_ordinal': 2, 'summary': '李宝珠一早出门买包子，回来撞见母亲在门口等她',
             'narration': _narration(),
             'beats': [
                 _beat('b02_01', '第二日清晨', '往外走。',
                       event='天亮后李宝珠推门出去', place='老宅院门',
                       names=['李宝珠'], ids=['char_001']),
                 # ⚠ 这两条 hinge 都挂在 b02_02 上而不是分给两拍：`hinge_drift` 判的是
                 #   「text 落在**本拍**的 span 原文内」，而「她买了两个…包子」在原文里
                 #   位于 b02_02 的区间里。挂错拍就是 `hinge_drift` 要抓的东西之一
                 #   ——底座在这里被它抓过一次（写这份底座时真红了）。
                 _beat('b02_02', '街上已经有', '那封信的事。',
                       event='她回来时母亲已经在门口等着', place='老宅院门',
                       names=['李宝珠', '母亲'], ids=['char_001', 'char_002'],
                       hinges=[{'kind': '硬数字', 'starts_with': '她买了两个',
                                'ends_with': '包子。', 'text': '她买了两个热腾腾的包子。'},
                               {'kind': '要害台词', 'starts_with': '其实母亲',
                                'ends_with': '那封信的事。',
                                'text': '其实母亲早就知道那封信的事。'}]),
             ]},
            {'chapter_ordinal': 3, 'summary': '李宝珠翻账册，管家来回米价，母亲给她添了一盏茶',
             'narration': _narration(),
             'beats': [
                 _beat('b03_01', '午后的日头', '一页页翻。',
                       event='午后李宝珠摊开账册翻看', place='老宅书房',
                       names=['李宝珠'], ids=['char_001']),
                 _beat('b03_02', '管家在门外', '米价又涨了。',
                       event='管家隔着门回话说米价涨了', place='老宅书房',
                       names=['李宝珠'], ids=['char_001'],
                       hinges=[{'kind': '因果前提', 'starts_with': '米价',
                                'ends_with': '又涨了。', 'text': '米价又涨了。'}]),
                 _beat('b03_03', '她皱着眉', '问问别家。',
                       event='她让管家去别家问价', place='老宅书房',
                       names=['李宝珠'], ids=['char_001']),
                 _beat('b03_04', '窗外的槐树', '一地碎叶。',
                       event='窗外槐树落了一地叶子', place='老宅书房',
                       names=[], ids=[]),
                 _beat('b03_05', '她忽然想起', '够不着桌子。',
                       event='她想起父亲从前也这样翻账', place='老宅书房',
                       names=['李宝珠'], ids=['char_001'], layer='recall'),
                 _beat('b03_06', '母亲进来给她', '没有喝。',
                       event='母亲进来添茶，她端起来没喝', place='老宅书房',
                       names=['李宝珠', '母亲'], ids=['char_001', 'char_002']),
             ]},
        ],
        '_provenance': {
            'scope_violations': [],
            'batches': [[1], [2], [3]],
            'context': {
                'architecture': 'bounded-fidelity-v2',
                'batch_size': 1,
                'stable_prefix': '0123456789',
                'current_chapter_full': True,
                'future_visible': False,
                'past_neighbor_chars_max': 4000,
                'continuity_chars_max': 1600,
                'past_names_chars_max': 600,
            },
            'char_refs': {'resolved': 12, 'pronoun_ok': 0, 'ambiguous': 0, 'late': 0,
                          'unknown': 0, 'invented': 0, 'no_names': 1, 'pronoun': 0},
            'verbatim_rewrites': [],
            'verbatim_recovered': [],
            'verbatim_failures': [],
            'partial_regen_chapters': [],
            'repairs': [],
        },
    }


def _hard_hits(gg: dict | None, bp: dict | None, mt: list | None) -> set[str]:
    """跑一遍判据 → **命中的 HARD 判据 id**。

    ⚠ 只收 `sev == 'hard'` 且**真有 findings** 的：`unver` 不算命中
    （那是判据的边界，不是数据的问题），度量更不算（它无条件产出）。
    """
    sev = {r.rule: r.sev for r in REGISTRY.all() if r.stage == 'stage2'}
    return {o.rule for o in checks.check(global_g=gg, blueprints=bp, main_text=mt)
            if sev.get(o.rule) == 'hard' and o.findings and o.unver_why is None}


# ─────────────────────────────────────────────────────────────────────────────
# 二、底座自己
# ─────────────────────────────────────────────────────────────────────────────

def test_the_clean_base_has_no_hard_hits():
    """⭐ **下面 37 条全部建立在这一条上。**

    一个本来就红的底座上，「改一处就红」什么也证明不了。
    """
    got = _hard_hits(global_grounding(), blueprints(), main_text())
    assert got == set(), f'干净底座上不该有任何 HARD 命中，实得 {sorted(got)}'


def test_canonical_unique_accepts_two_explicitly_distinguished_same_base_names():
    gg = global_grounding()
    chars = gg['entities']['characters']
    chars['char_001']['canonical'] = '书童(婆母的)'
    chars['char_002']['canonical'] = '书童(陆明洲的)'

    hits = _hard_hits(gg, blueprints(), main_text())
    assert 'canonical_unique' not in hits
    assert 'distinguisher_lonely' not in hits


def test_hedge_words_does_not_treat_factual_or_as_uncertainty():
    gg = global_grounding()
    character = gg['entities']['characters']['char_001']
    character['fate'] = '御医建议过继或让父母再生。'
    assert 'hedge_words' not in _hard_hits(gg, blueprints(), main_text())

    character['fate'] = '或许会让父母再生。'
    assert 'hedge_words' in _hard_hits(gg, blueprints(), main_text())


def test_the_clean_base_leaves_no_rule_unimplemented():
    """**注册了而没有实现调用它 ⇒ `skipped`，徽章至少 amber。**

    这一条盯的是 `checks.check()` 恰好产 71 个 `Outcome`——
    报告是由**判据注册表**生成的，不是由实现的输出生成的，
    所以漏实现一条不会让它从报告里消失，只会让它挂一行「实现未被调用」。
    """
    outs = checks.check(global_g=global_grounding(), blueprints=blueprints(),
                        main_text=main_text())
    want = {r.rule for r in REGISTRY.all() if r.stage == 'stage2'}
    assert {o.rule for o in outs} == want
    assert len(outs) == len(want) == 71


def test_global_field_failures_route_to_the_dedicated_resample_action():
    routed = {rule.rule for rule in REGISTRY.all()
              if rule.stage == 'stage2'
              and rule.fix == ('stage2.global', '--resample')}
    assert routed == {
        'canonical_unique', 'character_unattested', 'address_by_unresolved',
        'address_from_out_of_range', 'address_from_too_early',
        'address_bare_pronoun', 'address_all_pronoun', 'role_missing',
        'role_repeats_name', 'hedge_words', 'distinguisher_lonely',
        'world_rule_no_desc', 'secret_known_by_dangling',
    }


def test_the_two_unver_rules_always_speak():
    """两条 `unver` **每本每次都产**，与别的判据报绿还是报红无关。

    ⚠ `role_fate_unverifiable` 在原型里挂在 `if not leaky` 那一支上——
    `role_leaks_fate` 命中的那两本**根本走不到它**，于是「这一维没有确定性防线」
    这句话在最需要说的两本上反而不说。本仓改成无条件产出。
    """
    for gg in (global_grounding(), _with(lambda g, b, m: g['entities']['characters']
                                         ['char_001'].__setitem__('role', '她最终沦为妾室'))[0]):
        outs = {o.rule: o for o in checks.check(global_g=gg, blueprints=blueprints(),
                                                main_text=main_text())}
        assert outs['role_fate_unverifiable'].unver_why
        assert outs['secrets_absent'].unver_why


# ─────────────────────────────────────────────────────────────────────────────
# 三、36 条盘上 0 命中的 HARD —— 各配一份会让它红的样本
# ─────────────────────────────────────────────────────────────────────────────

Mutator = Callable[[dict, dict, list], Any]


def _with(fn: Mutator) -> tuple[dict | None, dict | None, list | None]:
    """在底座的**深拷贝**上改一处 → `(gg, bp, mt)`。

    改坏原件会让「改一处」变成「改了前面每一处」，而那种串味只在**测试顺序**变了
    之后才现形——最难查的一类。
    """
    gg, bp, mt = global_grounding(), blueprints(), main_text()
    got = fn(copy.deepcopy(gg), copy.deepcopy(bp), copy.deepcopy(mt))
    return got if isinstance(got, tuple) else (gg, bp, mt)


def _m(fn: Callable[[dict, dict, list], None]) -> Mutator:
    """把一个「就地改」的函数包成返回三元组的 mutator。"""
    def wrapped(gg: dict, bp: dict, mt: list):
        fn(gg, bp, mt)
        return gg, bp, mt
    return wrapped


def _ch(bp: dict, o: int) -> dict:
    return next(x for x in bp['blueprints'] if x['chapter_ordinal'] == o)


#: `(判据 id, 改哪一处, 为什么这一处会让它红)`。**一行一条**，36 条。
CASES: list[tuple[str, Mutator, str]] = [
    # ── 一、产物完整性与章对齐 ──────────────────────────────────────────
    ('upstream_missing', lambda g, b, m: (None, b, m),
     '两份产物缺一份。⚠ 判 FAIL 不判 note：什么都没查却打 PASS 是把两件事混为一谈'),
    ('section_id_leak', _m(lambda g, b, m: _ch(b, 1).__setitem__('section_id', 'sec_0001')),
     '章的唯一键全链只有一个，混进 section_id 之后下游按哪个键 join 要读代码才知道'),
    ('chapter_coverage', _m(lambda g, b, m: b['blueprints'].pop()),
     '少一章 blueprint'),
    ('chapter_order', _m(lambda g, b, m: b['blueprints'].reverse()),
     '章序倒过来。⚠ 集合对而顺序错，是分批产出年代的典型残留'),
    # ── 二、称谓表 ──────────────────────────────────────────────────────
    ('canonical_unique',
     _m(lambda g, b, m: g['entities']['characters']['char_002'].__setitem__('canonical', '李宝珠')),
     '两个 char_id 挂同一个显示名 ⇒ 下游按名字解析静默取到其中一个'),
    ('character_unattested',
     _m(lambda g, b, m: g['entities']['characters']['char_002'].update(
         canonical='凭空生造的人', address=[{'surface': '这个叫法原文没有', 'by': 'narration',
                                             'context': 'reference', 'from_chapter': 1}])),
     'canonical 与全部 surface 在原文里一个都搜不到 ⇒ 这个角色是编的'),
    ('address_by_unresolved',
     _m(lambda g, b, m: g['entities']['characters']['char_001']['address'][0]
        .__setitem__('by', 'char_999')),
     '`by` 指向 registry 里没有的 char_id。⚠ 载体那边的正则只管形状，跨产物只能是判据'),
    ('address_from_out_of_range',
     _m(lambda g, b, m: g['entities']['characters']['char_001']['address'][0]
        .__setitem__('from_chapter', 99)),
     '全书 3 章而 from_chapter=99 ⇒ 这条称呼永不下发，产物里却看着一切正常'),
    ('address_from_too_early',
     _m(lambda g, b, m: g['entities']['characters']['char_002']['address'][0]
        .__setitem__('from_chapter', 1)),
     '「母亲」原文 ch2 才首现而标 ch1。⚠ 单边：早于一定错，晚于合法'),
    ('address_all_pronoun',
     _m(lambda g, b, m: g['entities']['characters']['char_001'].__setitem__(
         'address', [{'surface': '她表姐', 'by': 'narration',
                      'context': 'reference', 'from_chapter': 1}])),
     '这个角色有据的叫法全都含代词 ⇒ 下游 display_name 只能取到相对说话人的说法。'
     '⚠ 与「整条是代词」分开：那一条删掉即可，这一条要补一条新的'),
    # ── 三、身份与结局 ──────────────────────────────────────────────────
    ('role_missing',
     _m(lambda g, b, m: g['entities']['characters']['char_001'].__setitem__('role', '')),
     '下游只下发 role，缺了写手不认识这个人。'
     '⚠⚠ **这条反例只有在判据吃 dict 时才构造得出来**——载体上它是 `min_length=1`'),
    ('role_repeats_name',
     _m(lambda g, b, m: g['entities']['characters']['char_001'].__setitem__('role', '李宝珠')),
     'role 与 canonical 完全同字 ⇒ 写手拿到的是「李宝珠：李宝珠」'),
    ('distinguisher_lonely',
     _m(lambda g, b, m: g['entities']['characters']['char_002']
        .__setitem__('canonical', '沈母（内院）')),
     '带了区分词而剥掉之后没有同名对象 ⇒ 区分词会被写手原样写进正文'),
    # ── 四、世界规则 · 秘密 ─────────────────────────────────────────────
    ('world_rule_no_desc',
     _m(lambda g, b, m: g['canon']['world_rules'][0]['versions'][0].__setitem__('desc', '')),
     '规则正文的唯一落点是空的，而下游取它时无兜底、缺失即显式终态'),
    ('secret_setup_order',
     _m(lambda g, b, m: g['_provenance'].__setitem__(
         'setup_chapters_pruned', [['secret_01', [2], []]])),
     '⚠⚠ **断言的是裁剪计数不是字段值**：裁剪跑在落盘前，'
     '再断言 setup_chapters 的值就是一条永远为真的判定'),
    ('secret_known_by_dangling',
     _m(lambda g, b, m: g['canon']['secrets'][0].__setitem__(
         'character_known_by', ['char_999'])),
     '`char_\\d+` 形状的知情人解析不出来。⚠ 自由文本（「在场士兵」）**放行**——按形状分流'),
    # ── 五、拍与区间 ────────────────────────────────────────────────────
    ('beat_id_unique',
     _m(lambda g, b, m: _ch(b, 2)['beats'][0].__setitem__('beat_id', 'b01_01')),
     'beat_id 是下游 join 的唯一键，重号则每一次 join 都可能取错拍'),
    ('beat_id_format',
     _m(lambda g, b, m: _ch(b, 1)['beats'][1].__setitem__('beat_id', 'b01_05')),
     '章内序不连续'),
    ('span_unlocatable',
     _m(lambda g, b, m: _ch(b, 1)['beats'][1]['span']
        .__setitem__('text', '这句话原文里根本没有')),
     '定位不到 ⇒ 这一拍没有量纲：字数、覆盖率、hinge 归属全部落空'),
    ('verbatim_failed',
     _m(lambda g, b, m: b['_provenance'].__setitem__(
         'verbatim_failures', [['b01_01', 'starts_with', '锚点不在本章原文里']])),
     '`_provenance.verbatim_failures` 非空即失败态。'
     '⚠ 它替掉了「靠 hinges[].text 的**缺席**去反推失败态」——失败态从此有正面的落点'),
    ('edge_hole',
     _m(lambda g, b, m: _ch(b, 3)['beats'].pop()),
     '章尾 32 字落在最后一拍之后。⚠ 章的**起因与结果**最常丢在这里，'
     '而被并掉的事在之后的每个环节里都不再存在'),
    ('beats_collapsed',
     _m(lambda g, b, m: (m[0].__setitem__('text', CH1 + '天黑之后院子里静得出奇。' * 24),
                         _ch(b, 1).__setitem__('beats', _ch(b, 1)['beats'][:1]))),
     '≥300 字的章只切了 1 拍。⚠ 锚用的是**契约自己的反例**，不跨书比'),
    ('beats_coarse',
     # ⚠ 留下的是**带 hinge 的那一拍**（`b03_02`）而不是第一拍：留第一拍会连带
     #   `chapter_no_hinge` 一起红，而那条与「切得太粗」是两件事，混在一份样本里
     #   会让人以为它们同源。
     _m(lambda g, b, m: _ch(b, 3).__setitem__('beats', _ch(b, 3)['beats'][1:2])),
     '章级 字/拍 超过**本书自己的中位数** 3 倍。⚠ 分母是本书的中位数不是跨书常量：'
     '拍数是同一个 prompt 都能改动 ±44% 的自由变量'),
    # ── 六、保真材料 ────────────────────────────────────────────────────
    ('hinge_cross_chapter',
     _m(lambda g, b, m: _ch(b, 1)['beats'][0]['hinges'].append(
         {'kind': '要害台词', 'source': _source(CH2, '街上已经有', '在吆喝。')})),
     '引的是别的章的原文，逐字无误但归错了章。'
     '⚠ 它几乎一定同时意味着该拍的 event 也越界了——而 event 会照原样发给写手'),
    ('hinge_anchor_void',
     _m(lambda g, b, m: (
         _ch(b, 1)['beats'][0]['hinges'].append({'kind': '物件', 'source': None}),
         b['_provenance']['verbatim_failures'].append(
             ['b01_01', 'hinge', '锚点不在本章原文']))),
     '锚点在本章原文里根本找不到（是转述或编的）——四分流里唯一一条根因在模型身上的'),
    ('hinge_drift',
     _m(lambda g, b, m: _ch(b, 1)['beats'][0]['hinges'].append({
         'kind': '要害台词',
         'source': {**_source(CH1, '院墙'), 'text': '这段文字全书哪一章都没有出现过'},
     })),
     'text 不在本 beat 的 span 原文内，而全书哪一章也找不到 ⇒ 位置对、字节错'),
    ('chapter_no_hinge',
     _m(lambda g, b, m: [be.__setitem__('hinges', []) for be in _ch(b, 2)['beats']]),
     '整章一条保真材料都没有 ⇒ 这一章的原文材料一条都没往下游走。⚠ 单拍为空是正常的'),
    # ── 七、在场与叙述 ──────────────────────────────────────────────────
    ('time_layer_no_present',
     _m(lambda g, b, m: [be.__setitem__('time_layer', 'recall')
                         for x in b['blueprints'] for be in x['beats']]),
     '全书 10 拍一条 present 都没有。⚠⚠ **枚举合法 ≠ 分布合理**——'
     '取值面那一半已被载体收走，而「每一条都合法、合起来不可能」载体表达不了'),
    ('present_ids_dangling',
     _m(lambda g, b, m: _ch(b, 1)['beats'][0].__setitem__('present_char_ids', ['char_999'])),
     '⚠⚠ 这是**解析器的后置条件**：present_char_ids 是代码解析出来的，'
     '违反它 ＝ 代码坏了，不是数据坏了'),
    ('present_names_invented',
     _m(lambda g, b, m: _ch(b, 1)['beats'][0].__setitem__('present_names', ['王二麻子'])),
     '全书原文里都找不到这个称呼 ⇒ 模型凭空造了一个人。'
     '⚠ 五分里唯一一条判 HARD 的——另四种去向各有一条度量'),
    ('scope_violation',
     _m(lambda g, b, m: b['_provenance'].__setitem__(
         'scope_violations', ['批 1 被要求产 ch1，实际产了 ch1 与 ch5'])),
     '非空即失败；产地必须无条件写这个键，没越界时写空列表'),
    ('event_empty',
     _m(lambda g, b, m: _ch(b, 1)['beats'][0].__setitem__('event', '')),
     '⚠ **判在本层不判 stage4**：留在下游意味着要先付掉一次规划调用才发现这一层产坏了'),
    ('summary_empty',
     _m(lambda g, b, m: _ch(b, 1).__setitem__('summary', '')),
     '契约说了它必填，而这一层没有任何东西在执行那句话'),
    ('repair_cache_policy',
     _m(lambda g, b, m: b['_provenance'].__setitem__('repairs', [{
         'chapter_ordinal': 2, 'cache_policy': 'default', 'fields': ['*'],
         'failure_items': ['span_unlocatable']}])),
     '⚠ 定向重生成的 prompt 与整跑时那一批**逐字相同** ⇒ 缓存必然命中 ⇒ '
     '一个字都没重新生成，而它照样打印「已重生成」'),
]


@pytest.mark.parametrize('rule,mutate,why', CASES, ids=[c[0] for c in CASES])
def test_a_sample_that_makes_this_hard_rule_red(rule, mutate, why):
    """⭐ **盘上 0 命中的 HARD 各一份会让它红的样本**（用户裁决 2026-08-12）。

    每一条只改底座的一处，然后断言**恰好那一条**红了。
    ⚠ 不断言「只有它红」：有几处改动会连带触发别的判据（改坏章序会连带 `beat_id_format`），
    而**那本来就是对的**——一份坏产物本来就该多报几条。
    要防的是反过来那件事：改了这一处而它**没红**。
    """
    gg, bp, mt = _with(mutate)
    got = _hard_hits(gg, bp, mt)
    assert rule in got, (
        f'改了这一处而 `{rule}` 没红：{why}\n'
        f'→ 这一次红的是 {sorted(got)}')


def test_every_hard_rule_without_a_disk_counterexample_has_a_synthetic_one():
    """⭐⭐ **守卫的守卫：这张清单不许悄悄变短。**

    38 条 HARD 里盘上只有 4 条有真反例；本文件要为**其余每一条**配一份。
    加一条 HARD 而不配样本，这条测试当场点名——
    否则那条新判据就又回到「从没见过反例」那一档，而它与一条不存在的判据长得一样。
    """
    #: 盘上**有真反例**的 4 条（`docs/schema/stage2.md` §5.5）。它们不需要合成样本
    #: ——真数据比合成样本强，而 `tests/test_corpus_fixtures.py` 一族在跑那三本。
    on_disk = {'address_bare_pronoun', 'role_leaks_fate', 'hedge_words', 'hinge_in_hole'}
    hard = {r.rule for r in REGISTRY.all() if r.stage == 'stage2' and r.sev == 'hard'}
    covered = {c[0] for c in CASES}
    assert len(hard) == 38, f'HARD 条数变了（{len(hard)}）——本条与 §5.1 的账要一起改'
    assert covered <= hard, f'CASES 里有不是 HARD 的：{sorted(covered - hard)}'
    assert not (missing := sorted(hard - on_disk - covered)), (
        f'这几条 HARD 既没有盘上反例、也没有合成样本：{missing}\n'
        f'→ **「盘上 0 命中」不是「干净」**，是那种失效模式一次都没发生过。'
        f'一条从没见过反例的判据与一条不存在的判据，在报告上长得一模一样')
    assert not (stale := sorted(covered & on_disk)), (
        f'这几条盘上已有真反例，不必再配合成样本：{stale}')
