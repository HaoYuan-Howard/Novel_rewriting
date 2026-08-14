#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage5_prompts.py —— **stage5 两条臂的 system 与原型逐字节相同。**

⚠ 本层比前四层多一层麻烦：**原型那两条 SYS 不是字符串字面量**，
是模块导入时 `一段三引号字面量 + LAYER_RULE + … + FIDELITY_RULE + …` 拼出来的，
而被拼进去的片段自己又是 `fill()` / `'\\n'.join([...])` 的结果。
⇒ `ast.literal_eval` 取不到它们，`_prototype_const` 那种取常量的写法在这里不够用。

⚠ 那个受限求值器**已抽进 `tests/proto_prompts.py`**（F5-3 起 stage2 是第二个消费方），
理由与「为什么不直接 import 原型模块」都写在那份文件的 docstring 里。
"""
from __future__ import annotations

import hashlib
import inspect

import pytest

from novel.stages.stage5 import prompts
from tests.proto_prompts import PROTO as _PROTO, wanter

#: 求值时要扫的原型文件。⚠ 顺序即优先级：**先出现的赢**，
#: 而五份文件里没有重名的模块级常量（重名会让这条测试拿错一份而不出声）。
_FILES = ('Stage5/stage5_common.py', 'Stage5/n5_write.py', 'Stage5/n6_write.py',
          'Stage4/n4_plan.py', 'Stage2/stage2_common.py')

_want = wanter(_FILES)


# ── 守卫的守卫：求值器本身要真的算出了东西 ────────────────────────────────────

def test_the_restricted_evaluator_actually_evaluates_something():
    """⚠ 求值器悄悄退化成「取到一个空串」时，下面每一条都会**恒绿**。

    这一条钉住它真的走过了 `BinOp` / `fill` / `join` 三条分支：
    两条 SYS 都是拼出来的（各 9~11 段），任一段丢失都会让长度掉一大截。
    """
    deep = _want('DEEP_SYS')
    assert len(deep) == 2724, f'原型 DEEP_SYS 渲染出 {len(deep)} 字——求值器少算了一段？'
    assert len(_want('SHALLOW_SYS')) == 2599


# ── 一、模板与渲染，两条臂各两条 ──────────────────────────────────────────────

@pytest.mark.parametrize('mode,const,proto,size', [
    ('deep', 'DEEP_SYS', 'DEEP_SYS', 2724),
    ('shallow', 'SHALLOW_SYS', 'SHALLOW_SYS', 2599),
])
def test_the_arm_system_is_a_byte_for_byte_port(mode, const, proto, size):
    """⭐ **两条臂的 system 与原型逐字节相同**，而且**钉的是发出去的那一份**。

    这两段压着 120+ 条真机 findings 的学费，**改写不会报错**——
    它只会让某一类失效重新出现，且要花一次真钱才看得见。

    ⚠ 本层的模板本身就是拼出来的，所以「模板逐字」与「渲染逐字」在这里合成了一条：
    `write_system()` 返回的就是 `_provenance.prompts.SYS` 记的那一份。
    """
    want = _want(proto)
    got = prompts.write_system(mode)
    assert got == want, (
        f'{const} 与原型分家了。'
        f'原型 sha1={hashlib.sha1(want.encode()).hexdigest()[:10]} '
        f'本仓 sha1={hashlib.sha1(got.encode()).hexdigest()[:10]}')
    assert got == getattr(prompts, const), f'`{const}` 与 `write_system({mode!r})` 分家了'
    assert len(got) == size          # 守卫的守卫：两边被同时改坏时才会一起漏


def test_stage5_has_one_prompt_implementation_without_experiment_selector():
    from novel.stages.stage5 import write

    assert list(inspect.signature(prompts.write_system).parameters) == ['mode', 'revise']
    assert list(inspect.signature(prompts.build_material).parameters) == [
        'entry', 'book', 'with_source_text']
    assert list(inspect.signature(write.run_write).parameters) == [
        'ctx', 'client', 'chapters']
    assert list(inspect.signature(write.run_revise).parameters) == [
        'ctx', 'client', 'chapters']


@pytest.mark.parametrize('name', ['LAYER_RULE', 'HINGE_LABEL_RULE', 'FIDELITY_RULE',
                                  'ORDER_RULE', 'STYLE_WORD_RULE', 'REVISE_TAIL'])
def test_every_shared_fragment_is_a_byte_for_byte_port(name):
    """⭐ 五段共用规则 + 修订尾巴，**逐段单独钉一次**。

    ⚠ 只钉合起来那两条**不够**：`LAYER_RULE` / `HINGE_LABEL_RULE` 是从
    `contracts.invariants` 的表 **join 出来**的，而 LESSONS 第 10 条那次事故的形状正是
    「模板一个字节没变，而搬进 invariants 的说明被转述了一遍」——
    合起来的长度对不上时，逐段这一条才说得出**是哪一段**漂了。
    """
    assert getattr(prompts, name) == _want(name)


def test_the_trim_system_is_a_byte_for_byte_port():
    """收缩那一次调用有自己的 system，**不拼在臂的 SYS 上**。

    它是本层真正的那道篇幅门（`length_over` 已由阻断降为提示）。
    """
    want = _want('TRIM_SYS')
    assert prompts.trim_system() == want == prompts.TRIM_SYS
    assert len(want) == 628


# ── 二、⚠⚠ 两个**有意留着**的洞 ────────────────────────────────────────────

def test_the_shallow_arm_still_has_no_layer_gloss():
    """⚠⚠ **`B-13` / `R-7.14`：开卷 system 里三个时空标签一个字的解释都没有。**

    开卷写手在 user 消息里照样收到「现场 / 回忆 / 转述」（下发面逐拍渲染 `LAYER_CN`），
    而 system 里没有 `LAYER_RULE`。**这是原型的现状，不是笔误。**

    ⇒ 本条不是在断言这样更好，是在钉住「补它是一次 prompt 变更」：
    顺手补上会让这条测试红，而那正是它该做的——那种改动要有一次真跑去证明
    它没把别的东西写坏，不该混在一次移植里悄悄发生。
    """
    assert prompts.LAYER_RULE in prompts.DEEP_SYS
    assert prompts.LAYER_RULE not in prompts.SHALLOW_SYS
    for cn in ('〔现场〕', '〔回忆〕', '〔转述〕'):
        assert cn not in prompts.SHALLOW_SYS, f'{cn} 出现在开卷 system 里——`B-13` 被人补了'


def test_the_revise_tail_is_the_third_fingerprint_and_has_only_one_producer():
    """⭐ 盘上有**三个** `prompts.SYS` 指纹：deep · shallow · 臂+`REVISE_TAIL`。

    ⚠ `_provenance.fixes` 只存 `[[rule, evidence_key]]` **引用**，指令由判据表生成；
    同理这条尾巴只许有一个产地——反哺那一侧再拼一次，`_provenance` 记的指纹
    与真正发出去的字节就会分家，而**下一轮到底发了哪句话**再也说不清。
    """
    fps = {hashlib.sha1(s.encode()).hexdigest() for s in (
        prompts.write_system('deep'), prompts.write_system('shallow'),
        prompts.write_system('deep', revise=True))}
    assert len(fps) == 3
    for mode in ('deep', 'shallow'):
        rev = prompts.write_system(mode, revise=True)
        assert rev == prompts.write_system(mode) + prompts.REVISE_TAIL
        assert rev.endswith('篇幅仍然要落在上面给定的区间内。\n')


# ── 三、词表整份到位（LESSONS 9 那次漂移的形状） ─────────────────────────────

def test_the_style_word_list_reaches_both_arms_whole():
    """⚠ **实测漂过一次**：两条臂各手抄了一份写法词表且互不一致——

    闭卷写 4 个词、开卷写同样 4 个外加两个**根本不在词表里**的。
    现在两条臂引的是同一个 `STYLE_WORD_RULE`，而它走 `fill()` 的 `$WORDS`
    ⇒ **手抄一个子集在结构上做不到**。本条是那件事的可执行形式。
    """
    from contracts.invariants import STYLE_WORDS
    assert len(STYLE_WORDS) == 15
    for w in STYLE_WORDS:
        assert w in prompts.STYLE_WORD_RULE, f'写法词「{w}」没进拆弹指令'
    for arm in (prompts.DEEP_SYS, prompts.SHALLOW_SYS):
        assert prompts.STYLE_WORD_RULE in arm
    assert '铺开心理历程' not in prompts.SHALLOW_SYS, '那是手抄时代多出来的词，不在词表里'


def test_the_invention_boundary_lists_all_seven_forbidden_kinds():
    """⚠ 发明边界的**七项平铺**与那条复合判据必须在同一段里。

    它们从前分居两处、切法还不同，而实测：**写手对同一条边界读到两个版本时
    会按更松的那个执行**。合并是并集不是替换。
    """
    from contracts.invariants import INVENT_ALLOW, INVENT_FORBID
    for item in (*INVENT_FORBID, *INVENT_ALLOW):
        assert item in prompts.FIDELITY_RULE, f'{item} 没进发明边界那一段'
    assert len(INVENT_FORBID) == 7 and '判据：' in prompts.FIDELITY_RULE


def test_the_four_fidelity_labels_are_explained_to_both_arms():
    """⚠ **覆盖面漂**（不是措辞漂）：`HINGE_LABEL_RULE` 从前只有闭卷有。

    开卷写手在 user 里照样收到这四种标签，system 里却没有任何解释——
    而**把四种都当成「必须逐字出现」是最常见的错法**。
    """
    from contracts.invariants import HINGE_LABEL_PARTS, hinge_heading
    for arm in (prompts.DEEP_SYS, prompts.SHALLOW_SYS):
        assert prompts.HINGE_LABEL_RULE in arm
    for kind, (_name, qual) in HINGE_LABEL_PARTS.items():
        assert f'【{hinge_heading(kind)}】' in prompts.HINGE_LABEL_RULE
        del qual                      # 限定语只进下发面的全串，system 这一份用短名
    assert '【必须成立，但【不必写在纸面上】】' not in prompts.HINGE_LABEL_RULE, \
        '小节标题套了两层【】——`hinge_heading` 那一步被跳过了'


def test_an_unknown_arm_blows_up_instead_of_falling_back():
    """⚠ `dict.get(mode, DEEP_SYS)` 那种兜底会让一次拼错的臂名**安静地跑闭卷**，
    而产物里的 `mode` 字段照样写着另一个值。⇒ 认不出当场炸。"""
    with pytest.raises(KeyError, match='deep'):
        prompts.write_system('deep_v2')
