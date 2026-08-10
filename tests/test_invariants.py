#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_invariants.py —— 盯住「唯一落点」这条纪律本身。

这些断言看着像废话，但它们各自对应一次真实的漂移：
枚举被手抄进 prompt 之后少了 8 个词、篇幅带被手抄之后漏了整整一档、
一个枚举的取值面在两处一开一闭。**同一件事的第 N 个落点必然分家。**
"""
from __future__ import annotations

import json
import os

import pytest

from contracts import invariants as inv

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'docs', 'design_brief', '05_samples')


def _enums():
    with open(os.path.join(SAMPLES, 'enums.json'), encoding='utf-8') as f:
        return json.load(f)


# ───────── 取值域闭合 ─────────

def test_state_domains_include_unknown_or_amber():
    """「不知道」与「没问题」必须可区分（需求 Q-2）。四个落点一个都不能少。"""
    assert 'unknown' in inv.FRESHNESS
    assert 'unknown' in inv.JOB_STATE
    assert 'amber' in inv.BADGE          # 通过了但有查不了的
    assert 'unver' in inv.SEVERITY       # 查不了，既不是通过也不是失败


def test_metric_is_separate_from_info():
    """度量与提示必须分开——度量无条件产出，不许挂在「没问题」那一支上。"""
    assert 'metric' in inv.SEVERITY and 'info' in inv.SEVERITY


def test_no_duplicates_in_any_tuple_domain():
    for name in dir(inv):
        val = getattr(inv, name)
        if name.isupper() and isinstance(val, tuple) and val and isinstance(val[0], str):
            assert len(set(val)) == len(val), f'{name} 里有重复值：{val}'


# ───────── 与现役后端对齐（样本是真实 API 响应，不是示意） ─────────

@pytest.mark.parametrize('const,key', [
    ('HINGE_KINDS', 'hinge_kinds'),
    ('TIME_LAYERS', 'time_layers'),
    ('OPS', 'ops'),
    ('STYLE_WORDS', 'style_words'),
    ('VOICE_KEYS', 'voice_keys'),
    ('HOOK_KEYS', 'hook_keys'),
])
def test_enum_matches_live_backend(const, key):
    """本模块的取值域必须与现役后端下发的那一份逐值相同。

    ⚠ 这条测试同时盯着前端：`docs/design_brief/05_samples/enums.json` 是前端 mock 的来源，
    它与本模块分家，就意味着界面上画的选项与后端判的不是一回事。
    """
    assert list(getattr(inv, const)) == _enums()[key]


def test_nonbody_kinds_is_prototype_plus_registered_additions():
    """`NONBODY_KINDS` 是**开放**枚举，所以它不走上面那条逐值相等的路。

    但「开放」不等于「随便改」：现役那份必须**恰好**等于
    `原型那四个 + NONBODY_KINDS_ADDED`。于是三件事同时被盯住：

      · 加了词却没登记 → 红。「有意加的」与「手滑加的」必须能区分
      · 删了词 → 红。存量产物里那个 `kind` 会读不回来
      · 顺序被打乱 → 红。加词只许追加

    ⚠ 加词的完整流程见 `R-1.7`：先过 `tools/probe_nonbody_word.py` 那道假阳闸门，
    再补一份合成样本，最后才动这两张表。
    """
    prototype = _enums()['nonbody_kinds']
    assert list(inv.NONBODY_KINDS) == prototype + list(inv.NONBODY_KINDS_ADDED), (
        '加词时要两处一起改：NONBODY_KINDS 末尾追加，NONBODY_KINDS_ADDED 里登记同一个词'
    )


def test_nonbody_kinds_never_collides_with_end_markers():
    """非正文标记与完结标记是**两条不同的路**，同一个词不许两边都算。

    完结标记只定切点、不进 `dropped`；非正文标记进 `dropped` 并记账。
    一个词落进两张表，同一段内容会同时按两种口径记账。
    ⚠ 实测踩过：候选词「正文完」在语料里有 1 处全等命中（`(正文完。)`），
    看着像非正文标记，其实它**已经是完结标记**。
    """
    both = set(inv.NONBODY_KINDS) & set(inv.END_MARKER_WORDS)
    assert not both, f'{both} 同时在两张表里'


def test_marker_strip_rules_are_valid_and_idempotent():
    """剥壳规则只有一份（判据 / 加词闸门 / 将来的 stage1 都从这里取），且必须是幂等的。

    不幂等意味着「剥两次」和「剥一次」结果不同——那样 `core` 的长度就取决于
    谁先调了它，而所有长度阈值判的都是 `core`。
    """
    import re
    lead, trail = re.compile(inv.NONBODY_LEAD_STRIP), re.compile(inv.NONBODY_TRAIL_STRIP)
    core = lambda s: trail.sub('', lead.sub('', s.strip()))
    for raw in ('【番外：两年后】', '19.后记', '(番外)', '15番外:药人', '番外', '  番外  '):
        assert core(core(raw)) == core(raw), f'{raw!r} 剥壳不幂等'
    assert core('【番外：两年后】') == '番外：两年后'
    assert core('19.后记') == '后记'


def test_op_band_matches_live_backend():
    live = {k: tuple(v) for k, v in _enums()['op_band'].items()}
    assert inv.OP_BAND == live
    assert inv.LOCKED_OP in inv.OPS


def test_hinge_groups_cover_every_kind_exactly():
    """保真类型 → 保真维度是**全映射**。缺一个就会走到兜底分支上去，

    而那个兜底分支正是原型踩过的坑：把写歪的类型兜底成「原样写入，一个字都不能改」，
    让一段叙述文成了逐字要求，全链没有一层出声。
    """
    assert set(inv.HINGE_GROUPS) == set(inv.HINGE_KINDS)
    assert len(set(inv.HINGE_GROUPS.values())) == len(inv.HINGE_KINDS)


def test_invent_guarded_covers_every_forbidden_item():
    """B-01 的机器可读形式：七项禁令逐项声明有没有探针。

    漏声明一项，就等于让它悄悄落进「不知道有没有查」——而那正是 B-01 要消灭的状态。
    """
    assert set(inv.INVENT_GUARDED) == set(inv.INVENT_FORBID)
    assert set(inv.INVENT_GUARDED.values()) <= {'full', 'partial', 'none'}


def test_invent_gap_is_still_five_items():
    """B-01 的现状：七项里有五项没有完整探针。

    ⚠ 这条测试**会随裁决而改**——补了探针就来改这个数，改不动说明还没补。
    它存在的意义是让「缺口有多大」这件事不靠记忆。
    """
    gap = [k for k, v in inv.INVENT_GUARDED.items() if v != 'full']
    assert len(gap) == 6, f'当前无完整探针的是 {gap}'
    assert [k for k, v in inv.INVENT_GUARDED.items() if v == 'none'] == \
           ['新道具', '新因果', '新地点', '新时间跳跃']


# ───────── 两个「故意不同」的数，别被顺手合并 ─────────

def test_beat_thresholds_stay_two_different_numbers():
    """碎拍吸收阈值 与 报警线 是**故意的**两个数：前者指导分预算，后者是报警。

    合并成一个会同时坏掉两件事。
    """
    assert inv.BEAT_MIN_CHARS != inv.BEAT_ALERT_MIN
    assert inv.BEAT_ALERT_MIN < inv.BEAT_MIN_CHARS < inv.BEAT_ALERT_MAX


# ───────── 节点表：命名裁决的守卫（Q-12 —— 守卫本身要有守卫） ─────────

def test_node_ids_are_stage_dot_verb():
    """作业 id 的形态是 `<stage>.<动词>`，且 stage 必须是真的 stage。

    ⚠ **动词不许是序数。** `n1`~`n9` 那套坏掉的根因就是把序数编进了标识符：
    加一个节点要么跳号、要么重号，而两者都不会报错。
    """
    for node, spec in inv.NODES.items():
        stage, _, verb = node.partition('.')
        assert verb, f'{node} 不是 `<stage>.<动词>` 形态'
        assert stage in inv.STAGES, f'{node} 的 stage={stage!r} 不在 STAGES 里'
        assert stage == spec.stage, f'{node} 的 id 与 spec.stage={spec.stage!r} 不一致'
        assert not verb.isdigit(), f'{node} 的动词是序数——那正是 n1..n9 坏掉的形态'


def test_node_labels_are_unique():
    """label 是**前端唯一的取处**。两个节点共用一句话，界面上就分不出点了哪个。"""
    labels = [s.label for s in inv.NODES.values()]
    assert len(set(labels)) == len(labels), f'label 有重复：{labels}'


def test_only_the_writing_node_has_modes():
    """臂是**参数**不是节点，而全链只有写作那一层有臂。

    ⚠ 别顺手给别的节点加 `modes`：`stage5.write` 的两臂之所以能当参数，
    是因为它们「只差 (system prompt, 给不给源章原文)，产物形状逐字节相同，判据表只有一张」。
    不满足这三条的东西合并成参数，就是把两件事藏进一个 id 里。
    """
    with_modes = {n: s.modes for n, s in inv.NODES.items() if s.modes}
    assert with_modes == {'stage5.write': inv.DEPTH_MODES}, \
        f'只有 stage5.write 该有 modes，且取值面就是 DEPTH_MODES，实得 {with_modes}'


def test_paid_flags_are_never_free_entries():
    """一个 flag 不能同时是「0 成本入口」和「让节点变付费」。

    `FREE_ENTRIES` 是 0 成本入口的唯一落点；`paid_flags` 是反方向的少数派。
    两边交叉就意味着**贵贱有两处在算**，而 `R-11.7` 要求它只有一处。
    """
    for node, spec in inv.NODES.items():
        overlap = set(spec.paid_flags) & set(inv.FREE_ENTRIES)
        assert not overlap, f'{node} 的 paid_flags 与 FREE_ENTRIES 撞了：{overlap}'
        unknown = set(spec.paid_flags) - set(inv.PAID_FLAGS)
        assert not unknown, f'{node} 的 paid_flags 有没登记在 PAID_FLAGS 里的：{unknown}'


def test_the_three_flag_tables_are_disjoint():
    """0 成本 / 已被取代 / 付费，三张表不许有同一个 flag。

    一个 flag 同时出现在两张表里，`is_paid` 的答案就取决于查表顺序——
    而**贵贱只有一处算**（`R-11.7`）。
    """
    free, sup, paid = (set(inv.FREE_ENTRIES), set(inv.SUPERSEDED_FREE_ENTRIES),
                       set(inv.PAID_FLAGS))
    assert not (free & sup), f'0 成本 ∩ 已取代：{free & sup}'
    assert not (free & paid), f'0 成本 ∩ 付费：{free & paid}'
    assert not (sup & paid), f'已取代 ∩ 付费：{sup & paid}'


def test_free_entries_covers_every_free_flag_the_live_backend_uses():
    """⚠ **这条测试就是那次漏登记本身。**

    2026-08-11 拿真实抓包对了一遍：现役后端有 **13 个** flag 被当 0 成本用，
    而 `FREE_ENTRIES` 当时只有 9 个，交集 8 个。
    漏登记的后果是**反的**——`is_paid` 拿这张表当白名单，未知 flag 一律按付费算，
    于是一个不花钱的动作会被画成橙色并弹二次确认。

    每个抓到的 `free_flag` 必须落在两张表之一：**现役**，或**已被两层命名取代且带标注**。
    """
    actions = _enums()['actions']
    live = {a['free_flag'] for a in actions if a['free_flag']}
    assert len(live) > 10, f'只抓到 {len(live)} 个 free_flag——样本多半换了形状，这条测试要跟着改'
    known = set(inv.FREE_ENTRIES) | set(inv.SUPERSEDED_FREE_ENTRIES)
    missing = sorted(live - known)
    assert not missing, (
        f'现役后端把这些 flag 当 0 成本用，而两张表里都没有：{missing}\n'
        f'→ 要么补进 FREE_ENTRIES，要么写进 SUPERSEDED_FREE_ENTRIES 并说清被什么取代了。'
        f'**不许只是不提它**——漏登记 = 界面上把免费动作画成付费'
    )


def test_a_paid_flag_is_never_captured_as_free():
    """反方向：登记成付费的 flag，不许在抓包里以 `free_flag` 的身份出现过。

    实测 `--repair-surface-habits` 在抓包里挂的正是 `paid=true` 的那条 action——
    两边一致。哪天不一致了，说明有一侧改了而另一侧没跟上。
    """
    actions = _enums()['actions']
    live_free = {a['free_flag'] for a in actions if a['free_flag']}
    liars = sorted(set(inv.PAID_FLAGS) & live_free)
    assert not liars, f'这些 flag 我们当付费登记，抓包里却是 0 成本入口：{liars}'


def test_free_nodes_are_the_ones_without_llm():
    """哪些节点默认不花钱，写死在这里——它是「预算大约几次调用」那句话的落点。

    ⚠ 这条测试**会随裁决而改**：加一个付费节点就来改这张表，改不动说明加错了地方。
    """
    free = {n for n, s in inv.NODES.items() if not s.paid}
    assert free == {
        'stage0.remove', 'stage0.approve', 'stage0.edit',
        'stage1.partition', 'stage4.context', 'stage5.review',
        'stage6.approve', 'stage7.assemble', 'stage7.approve',
    }, f'0 成本节点的集合变了：{sorted(free)}'
