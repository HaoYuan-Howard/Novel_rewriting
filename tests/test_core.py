#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_core.py —— `novel/core/` 里那些**确定性算子**。

这一份里的每一条都钉着**一次实测的账**，而不是「函数能跑」：
原型的这些函数是逐字搬过来的，搬错一个字符类、少一个前瞻断言，
结果不会报错——只会让某条判据在某类输入上**静默地不再拦**。
所以测的一律是**边界与反例**，不是 happy path。
"""
from __future__ import annotations

from contracts.invariants import ENDING_SHAPES, HOOK_APPROACHES, HOOK_APPROACH_NOTES, SEP_BANDS
from novel.core.bands import BAND_SEP, ending_shape, sep_band_of_text
from novel.core.facts import NUM_UNIT, secret_keywords
from novel.core.naming import invented_names, name_stems
from novel.core.normalize import (
    dominant_quote_style, normalize_paragraphs, normalize_punctuation, normalize_quotes,
    quote_pair,
)
from novel.core.text import TERMINAL_PUNCT, locate_norm, norm, window_overlap


# ── 段间分隔：那个 off-by-one 的触发条件 ──────────────────────────────────

def test_sep_band_reads_bytes_not_an_average():
    """⭐ **本层绕开上游 off-by-one 的那一条。**

    `paragraph_sep_chars` 把分隔字符总数除以**段数**，而分隔符只有「段数 − 1」个
    ⇒ 段数越少偏得越低。原型实测 3 段的空行分段文本算出 **1.33**，
    掉到 `1.5` 阈值以下 ⇒ 那本自己的文案段被判成「单换行」，归一化会照着把它拆散。

    这里钉住的是：**无论几段，只要有空行就是空行分段**。
    """
    for n in (2, 3, 4, 11):
        text = '\n\n'.join(f'第{i}段。' for i in range(n))
        assert sep_band_of_text(text) == '空行分段', f'{n} 段的空行分段被判反了'
        # 同样几段、只是换成单换行 —— 必须判成另一档
        assert sep_band_of_text(text.replace('\n\n', '\n')) == '单换行'


def test_band_sep_keys_match_the_published_value_domain():
    """`BAND_SEP` 的键 == `invariants.SEP_BANDS`（import 期已断言，这里让它在测试里也出声）。"""
    assert tuple(BAND_SEP) == SEP_BANDS


# ── 收尾形态：判定顺序是承重的 ────────────────────────────────────────────

def test_ending_shape_prefers_quoted_line_over_question():
    """⚠ **顺序有意**：既是台词又以问号收尾时算「引号台词」。

    原作里就有这一形（`“不然我怎么遇到你就变坏了呢？”`）。
    「用一句角色台词收尾」与「用叙述者的反问收尾」是两种不同的最后一击——
    前者是原作最常用的那种，后者是新文案最常落到的那种。**判反了这条判据就白做**。
    """
    assert ending_shape('“不然我怎么遇到你就变坏了呢？”') == '引号台词'
    assert ending_shape('她真的会来吗？') == '问句'
    assert ending_shape('他没有回头……') == '省略号'
    assert ending_shape('他没有回头。') == '陈述'
    assert ending_shape('   \n  \n') is None, '没有内容行时是 None，不是「陈述」'
    assert set(ENDING_SHAPES) >= {ending_shape(t) for t in
                                  ('「走。」', '走吗？', '走……', '走。')}


# ── 归一链 ────────────────────────────────────────────────────────────────

def test_punctuation_normalization_leaves_numbers_alone():
    """半角标点归一，**而数字里的分隔符不动**：千分位与时刻各有一条前瞻/后顾。"""
    assert normalize_punctuation('他说,好!真的?')[0] == '他说，好！真的？'
    assert normalize_punctuation('1,234 元')[0] == '1,234 元'
    assert normalize_punctuation('三点3:00出发')[0] == '三点3:00出发'
    assert normalize_punctuation('走了......')[0] == '走了……'
    assert normalize_punctuation('他走了.')[0] == '他走了。', '汉字后的句点才是句号'
    assert normalize_punctuation('圆周率3.14')[0] == '圆周率3.14', '小数点不动'


def test_quote_normalization_refuses_to_guess_when_pairing_fails():
    """⭐ **配对失败就整段原样返回**，问题进 `problems`——不猜。

    引号错乱多半是台词归属错位，猜一个「看起来对」的结果比不改更坏。
    ⚠ 同时钉住返回的改动数是 **0**：有 problems 时既没改文本，就不许记改动。
    """
    broken = '他说：「走。'
    out, n, problems = normalize_quotes(broken, '「」', '『』')
    assert out == broken and n == 0 and problems, '配对失败必须原样返回且出声'

    ok = '他说：“走『快点』。”'
    out, n, problems = normalize_quotes(ok, '「」', '『』')
    assert not problems and out == '他说：「走『快点』。」'
    assert n > 0


def test_quote_normalization_keeps_nesting_depth():
    """**盲互换会把嵌套层压成主引号**——所以是栈配对，不是正则替换。

    目标样式 `「」`，输入里主引号已经是 `「」` 而嵌套层是 `“”`：
    一个「把所有 `“` 换成 `「`」的正则会把嵌套层也变成主引号。
    """
    out, _n, problems = normalize_quotes('「他说“别动”。」', '「」', '『』')
    assert not problems
    assert out == '「他说『别动』。」', '嵌套层必须落到副引号上'


def test_paragraph_normalization_threshold_has_exactly_one_home():
    """`1.5` 只有 `normalize_paragraphs` 一个落点，`BAND_SEP` 只是把档位翻译成它认得的输入。"""
    assert normalize_paragraphs('甲。\n乙。', BAND_SEP['空行分段'])[0] == '甲。\n\n乙。'
    assert normalize_paragraphs('甲。\n\n乙。', BAND_SEP['单换行'])[0] == '甲。\n乙。'


def test_quote_pair_and_dominant_style_are_the_only_home():
    assert quote_pair('「」') == ('「」', '『』')
    assert quote_pair('“”') == ('“”', '‘’')
    assert quote_pair(None) is None
    assert dominant_quote_style('「甲」「乙」“丙”') == '「」', '按频次，不按表序'
    assert dominant_quote_style('没有引号') is None


# ── 重合率与回映 ──────────────────────────────────────────────────────────

def test_window_overlap_says_unmeasurable_not_zero():
    """⭐ 短于一个窗口 ⇒ **`None`（量不了）**，不是 `0.0`（没重合）。两者必须分得开。"""
    assert window_overlap('太短了', '一' * 100) is None
    assert window_overlap('一' * 30, '') is None, '没有源也是量不了'
    assert window_overlap('一' * 30, '一' * 40) == 1.0
    # 标点改写不能绕过：`norm` 空间里算
    assert window_overlap('甲，乙。' * 20, '甲乙' * 20) == 1.0


def test_locate_norm_maps_back_to_the_products_own_bytes():
    """⭐ **承重的回映**：归一后匹配得上 ≠ 原样出现过。

    凭据必须落回产物**自己的字节**——否则展示的是秘密原文，护栏自己成了泄漏源。
    """
    body = '他，走了——三步。'
    span = locate_norm('走了三步', body)
    assert span is not None
    assert body[span[0]:span[1]] == '走了——三步'
    assert norm(body[span[0]:span[1]]) == '走了三步'
    assert locate_norm('没出现过', body) is None
    assert locate_norm('，，，', body) is None, '归一后是空串 ⇒ 定位不了'


# ── 事实词法 ──────────────────────────────────────────────────────────────

def test_num_unit_skips_the_indefinite_article_and_bare_numerals():
    """裸数字大量是虚指，单独的「一」是不定冠词——两条都不算事实数字。"""
    got = [m.group() for m in NUM_UNIT.finditer('等了三年，四目相对，走了一半，打了二十板')]
    assert got == ['三', '二十']
    assert not [m.group() for m in NUM_UNIT.finditer('一封密折')], '「一」是不定冠词'


def test_secret_keywords_drops_numbers_and_short_fragments():
    """去数字（换个数字说法照样泄底）→ 按全角逗号切 → 留 ≥6 字。"""
    got = secret_keywords('他其实是三皇子，母亲当年被人换了孩子，很短')
    assert got == ['他其实是皇子', '母亲当年被人换了孩子']


# ── 发明检测 ──────────────────────────────────────────────────────────────

_CHARS = {
    'char_001': {'display_name': '苏清杳', 'name_distinctiveness': 'high'},
    'char_002': {'display_name': '我娘', 'name_distinctiveness': 'high'},
    'char_003': {'display_name': '掌柜', 'name_distinctiveness': 'common'},
}


def test_name_stems_takes_first_chars_of_real_names_only():
    """亲属称谓与 `common` 档都不进名字用字池。"""
    assert name_stems(_CHARS) == frozenset({'苏'})
    assert name_stems(None) == frozenset()


def test_invented_names_needs_the_left_anchor():
    """⭐ **没有左锚就是一场噪声**：原型实测无左锚 82 条候选、几乎全假。"""
    body = '苏二小姐来了，那位嬷嬷也在。'
    given = '苏清杳'
    assert invented_names(body, given, name_stems(_CHARS)) == ['苏二小姐']
    assert invented_names(body, given, frozenset()) == [], '没有池子就不报，不是报一堆'
    # 下发面里有的不算发明
    assert invented_names('苏二小姐来了。', '苏二小姐', name_stems(_CHARS)) == []


# ── 取值面 ────────────────────────────────────────────────────────────────

def test_hook_approach_notes_cover_exactly_the_approaches():
    """加一种写法而忘了写说明 ⇒ prompt 渲染期 `KeyError`，所以在这里先红。"""
    assert tuple(HOOK_APPROACH_NOTES) == HOOK_APPROACHES
    assert all(v.strip() for v in HOOK_APPROACH_NOTES.values())


def test_terminal_punct_has_one_home():
    """截断判定与删减守卫共用这一份——两处各抄一套就会漂。"""
    assert '。' in TERMINAL_PUNCT and '」' in TERMINAL_PUNCT and '.' in TERMINAL_PUNCT
    assert '，' not in TERMINAL_PUNCT
