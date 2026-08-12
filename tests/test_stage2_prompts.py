#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_prompts.py —— **stage2 三条付费臂的 system 与原型逐字节相同**，
除了三处由本仓契约逼出来的分家——那三处**双向**钉住。

⚠ 受限求值器在 `tests/proto_prompts.py`（与 `test_stage5_prompts.py` 共用一份）。
⚠⚠ **`_FILES` 里绝不许出现 `_legacy/`**：`_legacy/n2_semantic.py` 有
`BLUEPRINT_SYS`（`:252`）**与** `BLUEPRINT_HINT`（`:274`），与现役
`Stage2/n2_blueprint.py` 的 `:89` / `:224` **同名并存**（F4-5 那次只是改名，这次更深）。
而求值器那张文件表的语义是「**先出现的赢**」⇒ 放进去就会静默拿到旧世代
（它拼的是 `RULES` v1 不是 `RULES_V2`）。
光靠这条注释不够，所以另配了 `test_we_got_the_live_generation_not_the_legacy_one`。
"""
from __future__ import annotations

import hashlib

import pytest

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, CHANGE_TYPES_ENUM, HINGE_KINDS, NAME_DISTINCTIVENESS,
    NARRATIVE_PERSON, TIME_LAYERS, WORLD_RULE_KINDS,
)
from novel.stages.stage2 import prompts
from tests.proto_prompts import wanter

#: ⚠ 只列**现役**三份，`_legacy/` 一律不许进（见模块 docstring）。
_FILES = ('Stage2/stage2_common.py', 'Stage2/n2_global.py', 'Stage2/n2_blueprint.py')

_want = wanter(_FILES)


# ── 守卫的守卫：求值器真的算出了东西 ────────────────────────────────────────

def test_the_restricted_evaluator_actually_evaluates_something():
    """⚠ 求值器悄悄退化成「取到一个空串」时，下面每一条都会**恒绿**。

    三条 SYS 都是拼出来的（`RULES_V2 + fill(...)`，插值点共 8 个），
    任一段丢失都会让长度掉一大截。
    """
    assert len(_want('RULES_V2')) == 938
    assert len(_want('ROLE_FATE_RULE')) == 1527
    assert len(_want('GLOBAL_SYS')) == 8682
    assert len(_want('BLUEPRINT_SYS')) == 7448
    assert len(_want('ROLE_SPLIT_SYS')) == 2008


def test_we_got_the_live_generation_not_the_legacy_one():
    """⭐ **`_legacy` 里有同名的 `BLUEPRINT_SYS` / `BLUEPRINT_HINT`。**

    两代的区别是拼的开头：现役是 `RULES_V2`（938 字），旧世代是 `RULES`（v1）。
    这一条钉住我们拿到的是现役那一份——`_FILES` 哪天被顺手加进 `_legacy/`，
    它当场红，而不是安静地拿一份废弃的 prompt 去对账。
    """
    assert _want('BLUEPRINT_SYS').startswith(_want('RULES_V2'))
    assert _want('GLOBAL_SYS').startswith(_want('RULES_V2'))
    # 旧世代那个前缀叫 `RULES`，而它**不在**现役这三份文件里 ⇒ 求值器取不到它。
    with pytest.raises(KeyError):
        _want('RULES')


# ── 一、逐字节相同的那四段 ──────────────────────────────────────────────────

@pytest.mark.parametrize('name', ['RULES_V2', 'ROLE_FATE_RULE'])
def test_every_shared_fragment_is_a_byte_for_byte_port(name):
    """⭐ 两段共用片段**逐段单独钉一次**。

    ⚠ 只钉合起来那三条 SYS **不够**：`RULES_V2` 被两条抽取臂共用、
    `ROLE_FATE_RULE` 被 `GLOBAL_SYS` 与 `ROLE_SPLIT_SYS` 共用
    ——合起来的长度对不上时，逐段这一条才说得出**是哪一段**漂了
    （LESSONS 第 10 条那次事故的形状正是「模板一个字节没变，而被拼进去的那段被转述了」）。
    """
    assert getattr(prompts, name) == _want(name)


@pytest.mark.parametrize('name,size', [('GLOBAL_SYS', 8682), ('ROLE_SPLIT_SYS', 2008)])
def test_the_two_global_arms_are_byte_for_byte_ports(name, size):
    """⭐ `stage2.global` 那**两条**臂与原型逐字节相同。

    这两段压着 163 本语料 + 三本端到端实跑的学费，**改写不会报错**——
    它只会让某一类失效重新出现，且要花一次真钱才看得见。

    ⚠ 钉的是**渲染之后**那一份：它就是记进 `_provenance.prompts` 的那个指纹的输入
    （`_common.md` §一「指纹算在渲染结果上」）。本层是全链唯一一个
    **一个节点两个指纹键**的地方，所以两条都得钉。
    """
    want, got = _want(name), getattr(prompts, name)
    assert got == want, (
        f'{name} 与原型分家了。'
        f'原型 sha1={hashlib.sha1(want.encode()).hexdigest()[:10]} '
        f'本仓 sha1={hashlib.sha1(got.encode()).hexdigest()[:10]}')
    assert len(got) == size          # 守卫的守卫：两边被同时改坏时才会一起漏


# ── 二、⚠⚠ `BLUEPRINT_SYS` 的三处分家，双向钉住 ────────────────────────────
#
# 每一条都查**两件事**：本仓这一份没有它 · **而原型那一份有它**。
# 只查前者的话，原型哪天也把它删了，这三条测试会安静地留下来当摆设
# （判据 29/30：过期的描述不是不再有用，是主动误导）。

_DIVERGENCES = (
    # (原型里那段字节, 本仓换成的, 为什么)
    ('- narration（精简）：【数组】，每项 {last_beat_id, '
     'grammatical_person(first|third_limited|omniscient), narrator_name, '
     'focalizer_name, pronoun_surfaces[]}',
     '- narration（精简）：【数组】，每项 {grammatical_person(first|second|third), '
     'narrator_name, focalizer_name, pronoun_surfaces[]}',
     '字段表：去掉已裁撤的 `last_beat_id`，人称取值面换成 `NARRATIVE_PERSON`'),
    ('**按它们在本章里出现的先后排列**（各段止于哪儿由 `last_beat_id` 指定）。',
     '**按它们在本章里出现的先后排列**。',
     '分项那条里对 `last_beat_id` 的引用'),
    ('  · `last_beat_id`：**这一段止于哪条 beat**——填该段最后一条 beat 的 beat_id。\n'
     '    段界就落在那条 beat 的末尾，后面的 beat 属于下一段。\n'
     '    · 【末段填 null】最后一段止于章尾，不需要指定。\n'
     '    · 【只有一段时也填 null】整章就是一段，没有段界可言。\n'
     '    · **必须是本章真实存在的 beat_id**：它是段界的唯一表达，指错了段就分错了。\n',
     '',
     '整条 `last_beat_id` 说明（5 行）'),
)


def test_the_blueprint_arm_is_a_port_with_exactly_three_documented_divergences():
    """⭐⭐ **`BLUEPRINT_SYS` = 原型那一份 + 恰好这三处改动，一个字节不多。**

    把三处改动套到原型那一份上，结果必须与本仓这一份**完全相等**——
    这比「逐条查有没有某句话」强得多：它顺带证明**别的地方一个字节都没动**。
    """
    want = _want('BLUEPRINT_SYS')
    for old, new, why in _DIVERGENCES:
        assert want.count(old) == 1, f'原型里那段字节找不到（或不止一处）：{why}'
        want = want.replace(old, new)
    got = prompts.BLUEPRINT_SYS
    assert got == want, (
        f'BLUEPRINT_SYS 除三处登记的分家之外还有别的差异。'
        f'期望 sha1={hashlib.sha1(want.encode()).hexdigest()[:10]} '
        f'实际 sha1={hashlib.sha1(got.encode()).hexdigest()[:10]}')
    assert len(got) == 7181 == 7448 - 267


@pytest.mark.parametrize('old,new,why', _DIVERGENCES, ids=lambda v: str(v)[:24])
def test_each_divergence_is_pinned_in_both_directions(old, new, why):
    """⭐ 三处各自双向：**本仓没有它，而原型有它。**

    反方向那一半是防「原型也删了而这三条测试变成摆设」——
    真到那一天，本条会红，而红的含义是「去把这张分家表删掉」，不是「去改 prompt」。
    """
    assert old in _want('BLUEPRINT_SYS'), f'原型那一份里没有这段了 ⇒ 这处分家该退役：{why}'
    assert old not in prompts.BLUEPRINT_SYS, f'本仓这一份还留着它：{why}'
    if new:
        assert new in prompts.BLUEPRINT_SYS


def test_the_cut_field_is_absent_from_every_surface_the_model_sees():
    """⚠⚠ **`last_beat_id` 在 system 与结构提示里都不许出现。**

    只删 system 不删 `BLUEPRINT_HINT`（或反过来）是最糟的形态：
    模型照着提示产出那个键 ⇒ `StrictModel` 的 `extra='forbid'` 把**整份响应**判废
    ⇒ **再花一次钱**，而那本该在生成下一章之前就停住。
    """
    for surface in (prompts.BLUEPRINT_SYS, str(prompts.BLUEPRINT_HINT)):
        assert 'last_beat_id' not in surface
    # 退役的那两个人称取值同理：`Literal` 会当场拒收，同样是整份作废。
    for retired in ('third_limited', 'omniscient'):
        assert retired not in prompts.BLUEPRINT_SYS


def test_the_person_values_in_the_prompt_are_exactly_the_live_enum():
    """⭐ 人称那三个字面是**手写在模板里的**（原型也是这么写的，不走占位符）
    ⇒ 配一条会红的测试把它钉到 `NARRATIVE_PERSON` 上。

    ⚠ 不给它立一个 `$PERSON` 占位符是有理由的：那会是一个**原型没有**的占位符，
    而 `test_the_placeholder_census_is_complete` 那张全账是按「原型全仓用过 20 个」
    对死的。新造一个就得去动那个数，而那个数的意义正是「别自己发明占位符」。
    """
    assert NARRATIVE_PERSON == ('first', 'second', 'third')
    assert '(first|second|third)' in prompts.BLUEPRINT_SYS
    assert prompts.BLUEPRINT_HINT['blueprints'][0]['narration'][0][
        'grammatical_person'] == NARRATIVE_PERSON[0]


# ── 三、⚠⚠ `R-3.8b` 那条「prompt 侧待补」是假的 ─────────────────────────────

def test_the_chapter_head_rule_is_already_in_the_prompt_on_both_sides():
    """⭐⭐ **`R-3.8b` 记的「抽取 prompt 不但没说『第一拍要从章首起』，还明写允许空隙」
    是【假的】——两侧都明写着，而且带理由带例子。**

    实况：`BLUEPRINT_SYS` 里 `【不要为了首尾相接而拉伸】…允许有空隙` 的**下一条 bullet**
    就是 `【但空隙只能在中间，两头不许留】`。审计只读到上一条就停了。
    它是原型 2026-08-08 的 `b371244`（F-125）补上的，正为那 5 处章首漏抓——
    而本仓那份审计写在 2026-08-12，**晚四天**。

    ⇒ 教训因此反过来，而反过来的那个版本更值钱：
    **那 5 处漏抓是在 prompt 已经明说之后发生的** ⇒ 「写进 prompt」这条路
    没有遵守率保证，守它的只能是确定性判据（`edge_hole`）。
    ⚠ 所以**不要**照着 `R-3.8b` 的旧措辞再往 prompt 里补一句：那是一次没有收益的
    付费 prompt 变更，且会让这段文字出现同义的两处（LESSONS 第 11 条那一类）。
    """
    rule = '【但空隙只能在中间，两头不许留】'
    head = '第一条的 starts_with 就是本章的第一句话'
    tail = '最后一条的 ends_with 就是本章的最后一句话'
    proto = _want('BLUEPRINT_SYS')
    for probe in (rule, head, tail):
        assert probe in proto, f'原型那一份里没有 {probe!r}——这条结论要重新查'
        assert probe in prompts.BLUEPRINT_SYS, f'本仓这一份丢了 {probe!r}'
    # 「允许有空隙」那句也还在，两句是**一对**：后者限定前者，删掉任何一句都变味。
    assert '两条之间允许有空隙' in prompts.BLUEPRINT_SYS
    assert prompts.BLUEPRINT_SYS.index('两条之间允许有空隙') < prompts.BLUEPRINT_SYS.index(rule)


# ── 四、结构提示：取值面一个都不手抄 ────────────────────────────────────────

def test_the_hints_derive_every_enum_from_the_contract_layer():
    """⚠ 手抄的那一份会与载体分家，而分家的表现是
    「模型照着提示产出、载体当场拒收」——一次付费产出白花。
    """
    bp = prompts.BLUEPRINT_HINT['blueprints'][0]['beats'][0]
    assert bp['time_layer'] == '|'.join(TIME_LAYERS)
    assert bp['hinges'][0]['kind'] == '|'.join(HINGE_KINDS)
    ch = prompts.GLOBAL_HINT['entities']['characters']['char_001']
    assert ch['name_distinctiveness'] == '|'.join(NAME_DISTINCTIVENESS)
    assert ch['address'][0]['context'] == '|'.join(ADDRESS_CONTEXT)
    # `by` 的特殊取值 = `ADDRESS_BY` 去掉 `char_id`（那一格由前面的 `char_002` 示意）
    assert ch['address'][0]['by'] == 'char_002|' + '|'.join(
        b for b in ADDRESS_BY if b != 'char_id')
    canon = prompts.GLOBAL_HINT['canon']
    assert canon['world_rules'][0]['kind'] == WORLD_RULE_KINDS[0]
    assert prompts.GLOBAL_HINT['arc']['arc_tracks'][0]['change_type'] == CHANGE_TYPES_ENUM


def test_the_hints_never_ask_the_model_for_a_code_written_field():
    """⚠ `narrator_char_id` / `focalizer_char_id` 是**代码解析出来的**
    （`Narration` 上写着「代码写的。[HARD] 在 registry 或 null」）。

    把代码产的字段写进提示，模型会替我们猜一个——而猜出来的 `char_id`
    在 registry 里查不到，那条判据于是从「解析器的后置条件」退化成「模型的输出质量」。
    """
    for f in ('narrator_char_id', 'focalizer_char_id'):
        assert f not in str(prompts.BLUEPRINT_HINT)
        assert f not in prompts.BLUEPRINT_SYS


def test_the_three_hints_are_byte_identical_to_the_prototype_except_the_cut_field():
    """三份结构提示与原型逐键相同——`BLUEPRINT_HINT` 只差那个已裁撤的键。

    ⚠ 这一条是 `test_the_hints_derive_every_enum_from_the_contract_layer` 的另一半：
    那条查「取值面是派生的」，本条查「**形状**没漂」。两者差一个真实的故障：
    一份取值面全对、而少了一个键（或多了一层嵌套）的提示，模型会产出另一种形状。
    """
    assert prompts.GLOBAL_HINT == _want('GLOBAL_HINT')
    assert prompts.ROLE_SPLIT_HINT == _want('ROLE_SPLIT_HINT')

    want_bp = _want('BLUEPRINT_HINT')
    proto_nar = want_bp['blueprints'][0]['narration'][0]
    assert 'last_beat_id' in proto_nar, '原型那一份里没有这个键了 ⇒ 这处分家该退役'
    del proto_nar['last_beat_id']
    assert prompts.BLUEPRINT_HINT == want_bp
