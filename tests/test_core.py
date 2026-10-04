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

from novel.core.evidence import iter_global_evidence_slots, without_global_evidence
from novel.core.naming import (
    attested_surname, family_groups, invented_names, is_generic_appellation,
    name_stems, project_character_names,
    rename_semantic_markers, strip_distinguisher,
    surface_owners,
)
from novel.core.normalize import (
    dominant_quote_style, normalize_paragraphs, normalize_punctuation, normalize_quotes,
    quote_pair,
)
from novel.core.text import (
    SPLIT_MIN_SIDE, TERMINAL_PUNCT, locate_norm, min_hamming, norm, quote_count,
    split_by_speech_tag, window_overlap,
)


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
    """`1.5` 只有 `normalize_paragraphs` 一个落点；传入实测分隔均值（空行≈2.0／单换行≈1.0）。"""
    one = '甲。' + chr(10) + '乙。'
    two = '甲。' + chr(10)*2 + '乙。'
    assert normalize_paragraphs(one, 2.0)[0] == two
    assert normalize_paragraphs(two, 1.0)[0] == one


def test_quote_pair_and_dominant_style_are_the_only_home():
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


# ── 发明检测 ──────────────────────────────────────────────────────────────

_CHARS = {
    'char_001': {'display_name': '苏清杳', 'name_distinctiveness': 'high'},
    'char_002': {'display_name': '我娘', 'name_distinctiveness': 'high'},
    'char_003': {'display_name': '掌柜', 'name_distinctiveness': 'common'},
}


def test_character_name_projection_keeps_display_and_distinctiveness_together():
    """Stage4 下发与 Stage5 名称检查共用同一份角色名称投影。"""
    registry = {
        'char_001': {
            'canonical': '苏清杳',
            'name_distinctiveness': 'high',
            'aliases': [],
        },
        'char_002': {
            'canonical': '模型标签',
            'name_distinctiveness': 'common',
            'surfaces': [{'surface': '掌柜'}],
        },
    }
    projected, substitutions, unattested = project_character_names(
        registry, '苏清杳和掌柜都在。')

    assert projected == {
        'char_001': {
            'display_name': '苏清杳',
            'name_distinctiveness': 'high',
        },
        'char_002': {
            'display_name': '掌柜',
            'name_distinctiveness': 'common',
        },
    }
    assert substitutions == {'模型标签': '掌柜'}
    assert unattested == []


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

#: 六个角色，覆盖 `attested_surname` 各道过滤拦下的典型形状。
#: Characters v3：有据表面在 `surfaces` 键（v3 起不再回退 `aliases`）。
_REG = {
    'char_001': {'canonical': '孟青禾', 'surfaces': [{'surface': '阿禾'}]},
    'char_002': {'canonical': '孟青兰', 'surfaces': []},
    # ① 自造记账标签：「许言的姐姐」的「许」是**别人**的姓，碰巧对上纯属巧合
    'char_003': {'canonical': '许言的姐姐', 'surfaces': []},
    # ③ 通用角色词：「书童」同时挂着两个 char_id
    'char_004': {'canonical': '书童(陆明洲)', 'surfaces': [{'surface': '书童'}]},
    'char_005': {'canonical': '陆明洲', 'surfaces': [{'surface': '书童'}]},
    # 外貌修饰不是姓
    'char_006': {'canonical': '胖媒婆', 'surfaces': []},
}
_BOOK = '孟青禾 孟青兰 许言的姐姐 书童 陆明洲 胖媒婆'


def test_strip_distinguisher_has_one_home():
    """区分词是为了 registry 键唯一造的，**不是这本书的语言**。"""
    assert strip_distinguisher('李嬷嬷(慧贵妃宫)') == '李嬷嬷'
    assert strip_distinguisher('李嬷嬷（慧贵妃宫）') == '李嬷嬷'
    assert strip_distinguisher('孟青禾') == '孟青禾'


def test_attested_surname_五道过滤各拦一类():
    """⭐ 五道过滤里**只有多主那道**拦得住「书童」——它有据、长度也够。"""
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


def test_attested_pure_titles_do_not_become_a_family_but_suffixed_names_do():
    """实跑回归：medium/high 纯位号不能靠共同首字造出幽灵姓氏。

    过滤只认**整串**角色词；`王夫人` / `李将军` 仍须召回真姓，不能因复用
    同一语义词表而把带姓称谓一起删掉。
    """
    reg = {
        'char_001': {'canonical': '皇帝', 'name_distinctiveness': 'medium', 'address': []},
        'char_002': {'canonical': '皇后', 'name_distinctiveness': 'high', 'address': []},
        'char_003': {'canonical': '皇贵夫', 'name_distinctiveness': 'medium', 'address': []},
        'char_004': {'canonical': '皇太女', 'name_distinctiveness': 'high', 'address': []},
        'char_005': {'canonical': '王夫人', 'name_distinctiveness': 'medium', 'address': []},
        'char_006': {'canonical': '王姑娘', 'name_distinctiveness': 'high', 'address': []},
        'char_007': {'canonical': '李将军', 'name_distinctiveness': 'medium', 'address': []},
        'char_008': {'canonical': '李大人', 'name_distinctiveness': 'high', 'address': []},
    }
    book = '皇帝与皇后召见皇贵夫和皇太女。王夫人与王姑娘同行，李将军向李大人复命。'

    groups, sur, acct = family_groups(reg, book, surface_owners(reg))

    assert sur == {
        'char_001': None, 'char_002': None, 'char_003': None, 'char_004': None,
        'char_005': '王', 'char_006': '王',
        'char_007': '李', 'char_008': '李',
    }
    assert sorted(groups.values()) == [
        ['char_005', 'char_006'], ['char_007', 'char_008'],
    ]
    assert acct == {'families': 2, 'singleton_surnames': []}
    assert attested_surname(
        {'canonical': '皇甫珩', 'name_distinctiveness': 'high'}, '皇甫珩入殿。') == '皇', \
        '只能按完整位号相等排除，不能用皇字前缀误杀复姓'


def test_family_groups_needs_two_people_and_that_catches_the_fake_surnames():
    """⭐ 「≥2 人才成族」兜住剩余过滤漏掉的假姓——当前实测它们**全是单例**。

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
    assert 'role:皇太女' in rename_semantic_markers('皇太女')


# ⚠ 旧「叫法时点下发闸门」（`person_naming_surfaces` / `surface_first_chapter` /
# `rule_names_a_person` 与 `_GATE_REG`）已随 Stage2 语义审阅退役删除（2026-09）：
# 现役 Characters v2 契约明确「`identity_revealed_from` 不表示称谓首次能出现的章，
# 不能据此禁用称谓」，章内称谓出现与表面身份的选择由原文和 Blueprint 支持。


# ── 取值面 ────────────────────────────────────────────────────────────────
#
# ⚠⚠ 这里曾经有一条「Hook 写法说明覆盖在编配方」的用例（HOOK_APPROACHES
#    家族）。Stage6 三候选开篇文案面已随 STR-06 切换退役（F8a），取值面常量
#    整体删除；Hook 的必要选择进 Stage4 正式 Design，机械验由 stage5 hook
#    owner 的回执守护——本用例不再有现役职责。

def test_terminal_punct_has_one_home():
    """截断判定与删减守卫共用这一份——两处各抄一套就会漂。"""
    assert all(x in TERMINAL_PUNCT for x in ('。', '」', '】', ']', '］', '.'))
    assert '，' not in TERMINAL_PUNCT


def test_global_evidence_projection_removes_only_evidence_slots():
    data = {
        'entities': {
            'characters': {'char_001': {
                'role': '主角', 'role_evidence': {'source': {'text': '原文'}},
                'fate': None, 'fate_evidence': None,
            }},
            'relations': [{
                'relation_id': 'rel_01', 'predicate': '朋友',
                'predicate_evidence': {'source': {'text': '朋友'}},
            }],
        },
        'canon': {
            'setting': {
                '时代背景': '现代', 'era_evidence': {'source': {'text': '现代'}},
                '世界观': '现实', 'worldview_evidence': {'source': {'text': '现实'}},
            },
            'world_rules': [],
            'secrets': [{
                'secret_id': 'secret_01', 'fact': '秘密',
                'fact_evidence': {'source': {'text': '秘密'}},
                'knowledge_events': [{
                    'subject': {'kind': 'group', 'label': '众人'},
                    'known_from_chapter': 1,
                    'evidence': {'source': {'text': '众人皆知'}},
                }],
            }],
        },
        'source_blurb': {'facts': [{'kind': 'supported', 'claim_id': 'hook_001', 'claim': '主角变强', 'evidence': {'source': {'text': '变强'}}}], 'opening': {'kind': 'unresolved', 'reason': '本例只测剥证据'}},
    }

    paths = [path for path, _owner, _field in iter_global_evidence_slots(data)]
    assert 'entities.characters.char_001.role' in paths
    assert 'canon.secrets.secret_01.knowledge_events.0' in paths
    projected = without_global_evidence(data)
    assert projected['entities']['characters']['char_001']['role'] == '主角'
    assert 'role_evidence' not in projected['entities']['characters']['char_001']
    assert 'fate_evidence' not in projected['entities']['characters']['char_001']
    assert 'evidence' not in projected['canon']['secrets'][0]['knowledge_events'][0]
    assert data['entities']['characters']['char_001']['role_evidence']
