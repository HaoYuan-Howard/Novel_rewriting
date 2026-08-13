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
from novel.core.facts import NUM_UNIT, num_unit_keys, num_value, secret_keywords
from novel.core.naming import (
    attested_surname, family_groups, invented_names, is_generic_appellation,
    name_stems, person_naming_surfaces, rename_semantic_markers, rule_names_a_person,
    strip_distinguisher,
    surface_first_chapter, surface_owners,
)
from novel.core.normalize import (
    dominant_quote_style, normalize_paragraphs, normalize_punctuation, normalize_quotes,
    quote_pair,
)
from novel.core.spans import (
    HINGE_LCS_MIN, content_only, exact_span, extend_terminal_punct, locate_spans, walk_spans,
)
from novel.core.text import (
    SPLIT_MIN_SIDE, TERMINAL_PUNCT, locate_norm, min_hamming, norm, quote_count,
    split_by_speech_tag, window_overlap,
)


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


# ── 原话被提示语切开（`R-7.3b`）───────────────────────────────────────────

_SPLIT_SEG = '你若真要走，便再不必回来了。'


def test_a_speech_tag_split_still_counts_as_verbatim():
    """⭐ `「…，」他说，「…」` 是中文小说的**标准写法**，不是违规。

    按「原样出现」的字面判据它一定判缺失 ⇒ 放行，但返回**那处提示语**单独留痕：
    「一个字都没动」与「切成两截后逐字出现」不是同一件事。
    """
    body = '「你若真要走，」他放下茶盏，「便再不必回来了。」'
    assert split_by_speech_tag(norm(_SPLIT_SEG), body) == '，」他放下茶盏，「'


def test_the_gap_must_be_short_and_must_look_like_a_speech_tag():
    """两条准入各拦一类，少一条这个放行口就变成「判据不再拦」。"""
    long_tag = '「你若真要走，」他慢慢地放下手里那只温热的茶盏，「便再不必回来了。」'
    assert split_by_speech_tag(norm(_SPLIT_SEG), long_tag) is None, \
        '插一段动作描写不算提示语——那是改写，不是标准写法'
    rewritten = '他说你若真要走，然后又说便再不必回来了。'
    assert split_by_speech_tag(norm(_SPLIT_SEG), rewritten) is None, \
        '不是「收引号 + 短提示语 + 开引号」那个形状就不放行'


def test_a_short_fragment_is_refused_outright():
    """⚠ 两侧各至少 `SPLIT_MIN_SIDE` 个内容字：更短的碎片会在长正文里随便撞上，
    那就成了「把判据放宽到没有意义」，而不是「放行一个合法写法」。"""
    seg = '我不去你也别去'                       # 7 个内容字，切完必有一侧 < 4
    assert len(seg) < SPLIT_MIN_SIDE * 2
    assert split_by_speech_tag(seg, '「我不去，」他说，「你也别去。」') is None


def test_min_hamming_separates_a_typo_fix_from_a_missing_line():
    """「漏写了这句」与「只改了一两个字」是两件事——后者实测多半是纠正原文的错别字。
    ⚠ 等长窗口而非编辑距离：要查的就是「原样」。"""
    assert min_hamming('你若真要走', '……你若真要走……') == 0
    assert min_hamming('你若真要走', '你若真想走') == 1
    assert min_hamming('你若真要走', '完全不同的一句话') == 3, 'cap 以上不细算，返回 cap+1'
    assert min_hamming('你若真要走', '太短') == 3, '长度都不够就不必谈一字之差'


def test_quote_count_counts_lines_not_characters():
    """⭐ 它与 `dialogue_ratio` 都要：那一个量**占多少字**，这一个量**说了几句**。

    台词被压成转述时前者可以纹丝不动（叙述里复述一遍同样长），后者掉一半
    ——原型正是靠它抓到「闭卷把台词压成转述」这条效应量最大的跨书结论。
    """
    assert quote_count('「甲。」他说。「乙。」') == 2
    assert quote_count('他说甲，然后又说乙。') == 0
    assert quote_count('“甲。”和「乙。」') == 2, '四种引号体全表都数（归一前后都要成立）'


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


def test_num_value_reads_the_value_not_the_literal():
    """⭐ **这一条就是那次全量误报本身。**

    契约发给写手的原话是「数字必须保住，**措辞随你**」，而判据只搜阿拉伯字面
    ⇒ 成稿写「三十岁」被判成改了 `30`，**整章拦下**。古风正文里写「30」反而出戏，
    照那条判据去修数据必然让产物更差——那正是「判据错了」的信号。
    """
    assert num_value('三十') == num_value('30') == 30
    assert num_value('半') == 0.5
    assert num_value('两万') == 20000, '「万」要结算前面所有位'
    assert num_value('八') == 8


def test_num_value_returns_none_instead_of_guessing():
    """⚠ `None`（这串不是数）与 `0`（这串是零）**必须分得开**：
    调用方拿它当比对键，猜一个值出来就会把两个不同的事实判成同一个。"""
    assert num_value('第三者') is None and num_value('') is None
    assert num_value('〇') == 0, '「〇」是零，不是「解析不了」'


def test_num_unit_keys_are_valued_but_display_the_original_wording():
    """键按**值**取（`三十岁` 与 `30岁` 同键），展示串留正文里的原样——
    报出来的东西要是人在正文里找得到的那个写法。"""
    assert num_unit_keys('他三十岁') == {(30, '岁'): '三十岁'}
    assert num_unit_keys('他30岁') == {(30, '岁'): '30岁'}
    assert set(num_unit_keys('他三十岁')) == set(num_unit_keys('他30岁'))


def test_num_unit_keys_survives_a_number_at_the_very_end():
    """⚠ `m.end()` 正好落在串尾时，原型旧写法 `text[m.end()]` 直接 `IndexError`
    ——一份以数字结尾的成稿会让整条判据崩掉，而不是报出一个结论。"""
    assert num_unit_keys('打了二十') == {}, '没有单位就不是可核对的事实'
    assert num_unit_keys('打了二十板') == {(20, '板'): '二十板'}


def test_num_unit_keys_does_not_cut_success_into_a_fraction_unit():
    """「成」只有 0~10 才可能是成数；不能把「一百成功」截成「一百成」。"""
    assert num_unit_keys('好感度一百成功') == {}
    assert num_unit_keys('胜算八成') == {(8, '成'): '八成'}


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


# ── 姓氏族群（stage7 改名）─────────────────────────────────────────────────

#: 五个角色，覆盖 `attested_surname` 三道过滤各自拦下的那一类。
_REG = {
    'char_001': {'canonical': '孟青禾', 'address': [{'surface': '阿禾'}]},
    'char_002': {'canonical': '孟青兰', 'address': []},
    # ① 自造记账标签：「许言的姐姐」的「许」是**别人**的姓，碰巧对上纯属巧合
    'char_003': {'canonical': '许言的姐姐', 'address': []},
    # ③ 通用角色词：「书童」同时挂着两个 char_id
    'char_004': {'canonical': '书童(陆明洲)', 'address': [{'surface': '书童'}]},
    'char_005': {'canonical': '陆明洲', 'address': [{'surface': '书童'}]},
    # 外貌修饰不是姓
    'char_006': {'canonical': '胖媒婆', 'address': []},
}
_BOOK = '孟青禾 孟青兰 许言的姐姐 书童 陆明洲 胖媒婆'


def test_strip_distinguisher_has_one_home():
    """区分词是为了 registry 键唯一造的，**不是这本书的语言**。"""
    assert strip_distinguisher('李嬷嬷(慧贵妃宫)') == '李嬷嬷'
    assert strip_distinguisher('李嬷嬷（慧贵妃宫）') == '李嬷嬷'
    assert strip_distinguisher('孟青禾') == '孟青禾'


def test_attested_surname_四道过滤各拦一类():
    """⭐ 四道过滤里**只有多主那道**拦得住「书童」——它有据、长度也够。"""
    ow = surface_owners(_REG)
    assert sorted(ow['书童']) == ['char_004', 'char_005']
    got = {cid: attested_surname(v, _BOOK, ow) for cid, v in _REG.items()}
    assert got['char_001'] == '孟'
    assert got['char_002'] == '孟'
    assert got['char_003'] is None, '① 4 字以上的自造标签，首字不是姓'
    assert got['char_004'] is None, '③ 「书童」挂着两个人 —— ①② 都拦不住它'
    assert got['char_005'] == '陆'
    assert got['char_006'] is None, '「胖」是外貌修饰不是姓'
    # ⚠ 不在原文里的 canonical 一律判掉（过滤①）
    assert attested_surname({'canonical': '孟青禾'}, '', ow) is None


def test_common_titles_that_share_a_first_character_do_not_become_a_family():
    """实测回归：`太后` + `太傅` 不是姓太的一家人，不能下发 `太→新姓`。"""
    reg = {
        'char_001': {'canonical': '太后', 'name_distinctiveness': 'common', 'address': []},
        'char_002': {'canonical': '太傅', 'name_distinctiveness': 'common', 'address': []},
    }
    groups, sur, acct = family_groups(reg, '太后召见太傅。', surface_owners(reg))
    assert groups == {}
    assert sur == {'char_001': None, 'char_002': None}
    assert acct == {'families': 0, 'singleton_surnames': []}


def test_family_groups_needs_two_people_and_that_catches_the_fake_surnames():
    """⭐ 「≥2 人才成族」兜住了三道过滤漏掉的假姓——它们**全是单例**。

    ⚠ 单例**照样会被改名**，只是不进 `surname_map`。
    别把「不在族群表」读成「不改名」。
    """
    ow = surface_owners(_REG)
    groups, sur, acct = family_groups(_REG, _BOOK, ow)
    assert list(groups.values()) == [['char_001', 'char_002']], '只有「孟」够两个人'
    assert acct['families'] == 1
    assert acct['singleton_surnames'] == ['陆'], '单例姓记账里留痕，但不成族'
    assert sur['char_005'] == '陆' and 'char_005' not in sum(groups.values(), [])


def test_generic_appellation_defers_to_this_books_surnames():
    """⭐ **首字是本书的姓时，亲属字判定不成立**（`R-10.5`）。

    亲属字表里有 `孙`/`公`/`子`/`女`/`母`，而它们**同时是常见姓氏**。
    按「开头是亲属字就不改」去判，会让一个原作的姓氏活着进入「原创」成品。
    """
    assert is_generic_appellation('姐姐') is True
    assert is_generic_appellation('孙大娘') is True, '没把孙当姓的书里，它是亲属称呼'
    assert is_generic_appellation('孙大娘', {'孙'}) is False, '姓孙的大娘 —— 必须改'
    assert is_generic_appellation('七郎') is True, '排行不是名字'
    assert is_generic_appellation('孟青禾') is False
    assert is_generic_appellation('') is False


def test_rename_semantic_markers_preserve_rank_number_relationship_and_office():
    assert rename_semantic_markers('阿九') == rename_semantic_markers('玄九')
    assert rename_semantic_markers('阿九') != rename_semantic_markers('阿七')
    assert rename_semantic_markers('七郎') != rename_semantic_markers('六郎')
    assert rename_semantic_markers('李将军') == rename_semantic_markers('沈将军')
    assert rename_semantic_markers('李将军') != rename_semantic_markers('沈凌')
    assert rename_semantic_markers('沈凌') != rename_semantic_markers('沈将军'), \
        '新增原本没有的职位同样改变语义'


# ── 叫法时点（第四组，stage4 的下发闸门）─────────────────────────────────

_GATE_REG = {
    # 通名：它被登记成了角色，而「正室高于妾室」是**纯世界规则**
    'char_001': {'canonical': '正室', 'name_distinctiveness': 'common',
                 'address': [{'surface': '正室', 'from_chapter': 1}]},
    # 歧义：同一个叫法挂着两个人 ⇒ 它标识不了任何人
    'char_010': {'canonical': '皇帝', 'name_distinctiveness': 'high',
                 'address': [{'surface': '皇帝', 'from_chapter': 1}]},
    'char_011': {'canonical': '太上皇', 'name_distinctiveness': 'high',
                 'address': [{'surface': '皇帝', 'from_chapter': 1}]},
    # 真人：正典名 ch1 就有，而**另一个别名要到 ch8 才出现**
    'char_009': {'canonical': '谢允', 'name_distinctiveness': 'high',
                 'address': [{'surface': '龟公', 'from_chapter': 2},
                             {'surface': '百晓生', 'from_chapter': 8}]},
}


def test_a_common_noun_or_an_ambiguous_appellation_does_not_name_a_person():
    """⭐ 两道筛各修掉一类**误杀真规则**的错。

    拿「谁被这么叫过」的全集当人名探测器，「纳妾制度：正室高于妾室」
    与「皇帝有权赐婚」都会被当成情节整条剔掉。
    """
    naming = person_naming_surfaces(_GATE_REG)
    assert '正室' not in naming, '通名不是人名'
    assert '皇帝' not in naming, '一个叫法挂着两个人时，它标识不了任何人'
    assert naming.get('谢允') == 'char_009' and naming.get('百晓生') == 'char_009'


def test_the_gate_is_the_appellations_first_chapter_not_the_characters():
    """⭐⭐ **闸门用的是【叫法的时点】，不是【角色的时点】。**

    某本 `wr_06`「怡红院…实为百晓生所有…龟公等人员可能是卧底」= 一条 ch8 才揭示的
    秘密的全部内容。**按角色时点会放行**（那个角色 ch2 就在场、叫「龟公」），
    **按叫法时点才扣得住**——泄底的是「百晓生」这个叫法，它 ch8 才出现。
    """
    avail = surface_first_chapter(_GATE_REG)
    assert avail['龟公'] == 2 and avail['百晓生'] == 8
    naming = person_naming_surfaces(_GATE_REG)
    hit = rule_names_a_person('怡红院实为百晓生所有，龟公等人员可能是卧底。', naming)
    assert hit and max(avail[n] for n in hit) == 8, '放行章要取命中叫法里最晚的那一个'
    # 而一条真规则一个人都点不到 ⇒ 不扣
    assert rule_names_a_person('纳妾制度：正室高于妾室。', naming) == []


def test_the_naming_family_shares_one_key_space():
    """三个函数是**串起来用的**（`naming` 的命中拿去查 `avail`）——键空间差一格，
    闸门就会在某几个叫法上静默失效。"""
    assert set(person_naming_surfaces(_GATE_REG)) <= set(surface_first_chapter(_GATE_REG))


# ── 取值面 ────────────────────────────────────────────────────────────────

def test_hook_approach_notes_cover_exactly_the_approaches():
    """加一种写法而忘了写说明 ⇒ prompt 渲染期 `KeyError`，所以在这里先红。"""
    assert tuple(HOOK_APPROACH_NOTES) == HOOK_APPROACHES
    assert all(v.strip() for v in HOOK_APPROACH_NOTES.values())


def test_terminal_punct_has_one_home():
    """截断判定与删减守卫共用这一份——两处各抄一套就会漂。"""
    assert all(x in TERMINAL_PUNCT for x in ('。', '」', '】', ']', '］', '.'))
    assert '，' not in TERMINAL_PUNCT


# ── `span` 锚点的 `recover` 臂（F5-2）─────────────────────────────────────
#
# 原型注释记着「实测 5 次」模型照抄锚点时改掉一两个字，下面两例逐字搬自那份注释。
# ⚠ 每一条测的都是**边界与反例**：`recover` 这条臂真正的风险不是「救不回来」，
#   而是「**该报错的时候把错盖掉了**」。

_TEXT = '定然不会善罢甘休。她转身就走，难产的我说过一句话都算多。'


def _bps(starts: str, ends: str) -> list[dict[str, object]]:
    return [{'chapter_ordinal': 1,
             'beats': [{'beat_id': 'b01_01', 'span': {'starts_with': starts, 'ends_with': ends}}]}]


def _walk(starts: str, ends: str, *, recover: bool):
    return list(walk_spans(_bps(starts, ends), {1: _TEXT}, recover=recover))


def test_a_damaged_anchor_is_a_failure_when_recover_is_off():
    """⭐⭐ **这一条是本臂的主守卫，不是那条「救得回来」。**

    `recover=False` 时锚点差一个字就必须报「找不到」——校验器走的正是这条路。
    开着 `recover` 跑校验，「能定位」会把「**锚点不是原文字节**」这件事整个盖掉，
    而那是 `anchor_not_verbatim` 与 `_provenance.verbatim_recovered` 唯一要说的话。
    """
    (_o, _be, hit, prob) = _walk('都不会善罢甘休', '算多', recover=False)[0]
    assert hit is None
    assert 'starts_with' in prob and '找不到' in prob


def test_recover_returns_the_fragment_it_actually_matched_not_the_anchor():
    """`recover=True` 时按最长公共子串救回来，**而返回的是实际匹配到的那一截**。

    ⚠ 第二个返回值必须被 `walk_spans` 接回去：救回来的片段比原锚点短，
    而它的 `len()` 是区间算术的输入——不接就是一串**错位且不报错**的原文偏移。
    这里正面钉住区间落在原文里对的位置上。
    """
    (_o, _be, hit, prob) = _walk('都不会善罢甘休', '算多', recover=True)[0]
    assert prob is None
    a0, a1, _b0, _b1 = hit
    # `'都不会善罢甘休'` 的 LCS 是 `'不会善罢甘休'`（6/7 = 0.86 ≥ 0.8，且 6 ≥ 4）。
    # ⚠ 区间**带着句末的「。」**：`extend_punct` 对救回来的片段一样生效，
    #   而那是对的——hinge 要逐字发给下游，削掉句末标点就是发出去一句不完整的原话。
    assert _TEXT[a0:a1] == '不会善罢甘休。'


def test_the_prototypes_own_second_example_is_NOT_recoverable_by_its_own_thresholds():
    """⭐⭐ **原型注释举的两例里，只有一例救得回来——而这不是 bug，是双门槛在做它该做的事。**

    第二例 `'难产的对我说过'` ← 原文 `'难产的我说过'`（模型**插**了一个字，
    上一例是**改**字）：最长公共子串只有 `'难产的'` **3 字**，
    相对覆盖率 3/7 = **0.43**，绝对长度 3 < 4 ⇒ **两道门都不过**。

    ⚠ 那段注释写的是「救的是同一类反复出现的损坏（**实测 5 次**：…）」——
    括号里列的是**观察到的损坏**，不是「这几例都救回来了」。照字面读成后者，
    就会去把门槛调低到能救它，而调低的代价是**把猜出来的字节当成原文发给写手**。

    ⇒ 这一类的正确出路不是救，是**报**：它进 `_provenance.verbatim_failures`
    （非空即失败态）。那个键正是为这一类存在的——在它之前，
    「这份产物是不是失败态」只能靠遍历全部 hinge 去**反推一个缺席**。
    """
    (_o, _be, hit, prob) = _walk('定然', '难产的对我说过', recover=True)[0]
    assert hit is None
    assert 'ends_with' in prob and '找不到' in prob


def test_both_recover_thresholds_are_and_not_or():
    """⭐ 两道门是**与**关系，各拦一类。少一道就有一类假救回。

    · 只有相对门槛（`HINGE_LCS_MIN`）时：两字锚点对上一个字就是 0.5，
      三字锚点对上两个字就是 0.67——**短锚点太容易凑**。绝对门槛（≥4 字）拦它。
    · 只有绝对门槛时：一个 20 字锚点里对上 4 个字也算救回来了。相对门槛拦它。
    """
    assert HINGE_LCS_MIN == 0.8
    # ① 绝对门槛拦下的：'的我说' 与原文 '的我说' 本来就在——换成 '的他说'，
    #    LCS = '说' 1/3，两道门都不过。
    assert _walk('定然', '的他说', recover=True)[0][2] is None
    # ② 相对门槛拦下的：锚点前半整段是编的，只有尾巴 4 个字对得上 ⇒ 4/12 = 0.33。
    assert _walk('定然', '完全不相干的八个字算多', recover=True)[0][2] is None


def test_exact_source_span_checks_offsets_text_normalization_and_order():
    raw = '甲说：“原样材料。”乙离开。'
    start = raw.index('原样材料')
    end = raw.index('”', start) + 1
    source = {
        'text': raw[start:end],
        'start': start,
        'end': end,
        'normalized': content_only(raw[start:end]),
        'recovery': 'exact',
    }
    assert exact_span(source, raw) == (start, end, None)
    assert (exact_span({**source, 'text': '被改过'}, raw)[2]
            == 'source span text does not match offsets')
    assert (exact_span({**source, 'normalized': '被改过'}, raw)[2]
            == 'source span normalized text does not match')
    assert 'after' in exact_span(source, raw, after=start + 1)[2]


def test_terminal_material_suffix_keeps_ascii_period_but_stops_before_quote_boundary():
    raw = 'material.:「next」'
    end = extend_terminal_punct(raw, len('material'))
    assert raw[:end] == 'material.'


def test_locate_spans_accepts_verified_exact_sources_without_searching():
    raw = '第一拍结束。第二拍结束。'
    split = raw.index('第二拍')
    spans = []
    for bid, start, end in (('b01_01', 0, split), ('b01_02', split, len(raw))):
        text = raw[start:end]
        spans.append({
            'beat_id': bid,
            'span': {
                'text': text, 'start': start, 'end': end,
                'normalized': content_only(text), 'recovery': 'exact',
            },
        })
    located, problems, loose = locate_spans(
        [{'chapter_ordinal': 1, 'beats': spans}], {1: raw})
    assert located == {1: [('b01_01', 0, split), ('b01_02', split, len(raw))]}
    assert problems == [] and loose == 0


def test_locate_spans_does_not_expose_recover_at_all():
    """⭐ **量纲侧与校验侧根本没有这个旋钮，那个默认值是终态不是「还没接」。**

    `locate_spans` 是 stage4 算 `source_chars` 与本层判 `span_unlocatable` 共用的入口。
    给它开一个 `recover` 参数，下一次就会有人为了「让红灯变绿」把它打开——
    而那一下会同时改掉**下游写手真正收到的字节**。
    ⇒ 这一条钉的是签名：`recover` 只在 `walk_spans` 上，且只有落盘路会传 `True`。
    """
    import inspect
    assert 'recover' not in inspect.signature(locate_spans).parameters
    p = inspect.signature(walk_spans).parameters['recover']
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is False
    # 而它确实还是那条会报错的路：坏锚点在 `locate_spans` 上照旧进 `problems`
    _spans, problems, _loose = locate_spans(_bps('都不会善罢甘休', '算多'), {1: _TEXT})
    assert len(problems) == 1
