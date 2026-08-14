#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_prompts.py —— 插值机器。

⚠ **这一份守的是本项目实测漂过两次的那个病**：
写法词表的 prompt 版写着「象征/强化感官」而真词表是「意象/感官」、**另 8 词一个没提**；
篇幅带的 prompt 版**漏了整整一档**。
**模型按 A 算、校验按 B 判，谁也不会出声。**
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from contracts import invariants as inv
from novel.llm.prompts import (
    ENUM_BLOCKS, fill, required_top_level,
)

# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 每个进 prompt 的词表，**整份**都要在渲染出来的块里
# ─────────────────────────────────────────────────────────────────────────────

#: `块名 → 它的源`。**逐条声明，不自动发现**：自动发现会让「哪些表进了 prompt」
#: 取决于命名巧合，而这张表要能被人读着核对。
_SOURCES: dict[str, tuple] = {
    'KINDS': inv.HINGE_KINDS,
    'LAYERS': inv.TIME_LAYERS,
    'RULE_KINDS': inv.WORLD_RULE_KINDS,
    'NAME_DISTINCT': inv.NAME_DISTINCTIVENESS,
    'CONTEXTS': inv.ADDRESS_CONTEXT,
    'BY_SPECIAL': tuple(b for b in inv.ADDRESS_BY if b != 'char_id'),
    'STYLE_WORDS': inv.STYLE_WORDS,
    'WORDS': inv.STYLE_WORDS,
    'ALLOW': inv.INVENT_ALLOW,
    'FORBID': inv.INVENT_FORBID,
}


@pytest.mark.parametrize('name', sorted(_SOURCES))
def test_every_member_of_the_table_reaches_the_prompt(name):
    """⭐ **每一个成员都要出现在渲染结果里。**

    这条断言的形状就是那次事故的形状：`STYLE_WORDS` 有 15 个词，
    而 prompt 里只提了 2 个——**而它跑起来一点问题都没有**，
    只是模型再也不知道另外 13 个词是禁用的。
    """
    block, source = ENUM_BLOCKS[name], _SOURCES[name]
    missing = [m for m in source if m not in block]
    assert not missing, f'`${name}` 的渲染漏了 {missing}（表里共 {len(source)} 项）'
    assert len(source) >= 2, f'{name} 只有 {len(source)} 项——这条断言就失去判别力了'


def test_style_words_is_the_one_that_actually_drifted():
    """把那次事故写成一条具体的断言：**15 个词，一个不少**。"""
    assert len(inv.STYLE_WORDS) == 15
    for w in inv.STYLE_WORDS:
        assert f'「{w}」' in ENUM_BLOCKS['STYLE_WORDS']
        assert w in ENUM_BLOCKS['WORDS']
    assert '意象' in ENUM_BLOCKS['STYLE_WORDS'], 'prompt 版一度写的是「象征」'


def test_the_length_band_block_carries_both_ends():
    """⚠ 另一次实测漂移：篇幅带的 prompt 版**漏了整整一档**。"""
    lo, hi = inv.PLAN_BUDGET_BAND
    assert ENUM_BLOCKS['BANDS'] == f'{lo:g}~{hi:g}×'
    assert str(lo) in ENUM_BLOCKS['BANDS'] or f'{lo:g}' in ENUM_BLOCKS['BANDS']


def test_two_renderings_of_one_table_are_not_two_tables():
    """`$STYLE_WORDS` 与 `$WORDS` 是同一张表的两种渲染。**改表两边一起变。**"""
    assert set(inv.STYLE_WORDS) <= set(ENUM_BLOCKS['WORDS'].split(' / '))


# ─────────────────────────────────────────────────────────────────────────────
# 二、`fill()` —— 选型判据只有一条：缺值时的行为
# ─────────────────────────────────────────────────────────────────────────────

def test_a_missing_placeholder_raises_instead_of_rendering_empty():
    """⚠⚠ **这一条就是整个选型判据。**

    Jinja2 那类默认把未定义渲染成空串，于是模型收到一句
    「不许使用以下词：」后面**什么都没有**——它照样会回答，而没有任何一层出声。
    """
    with pytest.raises(KeyError):
        fill('你必须遵守 $NOT_A_BLOCK 这条')


def test_safe_substitute_is_never_called():
    """`safe_substitute` 就是 Jinja2 那个行为（未定义 → 空串）。**一次都不许调。**

    ⚠ 按 **AST** 查而不是按字符串查：本模块的注释里就写着这个词
    （它解释的正是「为什么不用它」），按字符串查会把那段解释判成违规——
    **一条会误报的守卫等于一条迟早被删掉的守卫。**
    """
    import ast
    import inspect
    from novel.llm import prompts
    tree = ast.parse(inspect.getsource(prompts))
    calls = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == 'safe_substitute']
    assert not calls, f'第 {calls} 行调了 safe_substitute'


def test_enum_blocks_are_merged_so_a_hand_written_subset_is_impossible():
    """调用点不必也不许自己传枚举块——**手抄一个子集在结构上做不到**。"""
    out = fill('禁用：$WORDS\n保真类型：$KINDS')
    for w in inv.STYLE_WORDS:
        assert w in out
    for k in inv.HINGE_KINDS:
        assert k in out


def test_a_call_site_may_not_shadow_a_block():
    """传一个与块同名的 kwarg 会被**拒**：那是同一个语义的第二个落点，
    而静默覆盖比手抄更难发现（它连 diff 都看不出来）。"""
    with pytest.raises(ValueError, match='唯一落点|不许自己造'):
        fill('$WORDS', WORDS='我自己编的词表')


def test_an_escaped_dollar_survives_substitution_and_gets_caught():
    """⚠ 三类 `$` 事故里，只有 **`$$`** 会活着走出 `substitute`——它当成转义，
    安静地渲染成一个字面 `$`，**不报错**。而这些 prompt 正文里 `$` 出现 0 次，
    所以活下来的每一个都该被看见。"""
    with pytest.raises(ValueError, match=r'没被替掉'):
        fill('价格是 $$99')


def test_an_invalid_placeholder_is_caught_by_substitute_itself():
    """另外两类由 `Template` 自己抛：拼错的键 → `KeyError`；`$` 后跟非标识符 → `ValueError`。
    ⚠ 登记在这里是为了说清**边界在哪**——不然读的人会以为上面那条守着全部三类。"""
    with pytest.raises(ValueError, match='Invalid placeholder'):
        fill('价格是 $99')


def test_normal_interpolation_still_works():
    assert fill('书名《$TITLE》，禁用 $WORDS', TITLE='测试书') .startswith('书名《测试书》')


# ─────────────────────────────────────────────────────────────────────────────
# 三、`required_top_level`
# ─────────────────────────────────────────────────────────────────────────────

def test_required_top_level_uses_the_alias_not_the_field_name():
    """响应是 JSON，`require` 要拿去查 **JSON 的顶层键**。"""
    class M(BaseModel):
        provenance: dict = Field(alias='_provenance')
        opt: str | None = None

    assert required_top_level(M) == ('_provenance',)


def test_required_top_level_skips_anything_with_a_default():
    class M(BaseModel):
        a: int
        b: int = 1
        c: str | None = None

    assert required_top_level(M) == ('a',)
