#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_invariants.py —— 盯住「唯一落点」这条纪律本身。

这些断言看着像废话，但它们各自对应一次真实的漂移：
枚举被手抄进 prompt 之后少了 8 个词、篇幅带被手抄之后漏了整整一档、
一个枚举的取值面在两处一开一闭。**同一件事的第 N 个落点必然分家。**
"""
from __future__ import annotations

import pytest

from contracts import invariants as inv
from tests import legacy_names as legacy

#: 抓包的读取与「原型名 → 新节点」的翻译**只有一份**，在 `tests/legacy_names.py`。
_enums = legacy.captured_enums


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


# ───────── 跑法面（骨架 D 新增：`NodeSpec.flags` / `VALUED_FLAGS` / `WRITE_EFFECTS`） ─────────

def _known_flags():
    return set(inv.FREE_ENTRIES) | set(inv.PAID_FLAGS) | set(inv.VALUED_FLAGS)


def test_node_flags_are_registered_run_modes():
    """一个节点接受的 flag，必须在三张表之一里有名字。

    ⚠ **没名字的 flag 会让 `is_paid` 说不出话**：它拿 `FREE_ENTRIES` 当白名单，
    认不出的一律按付费算（`Q-10`）——于是一个不花钱的动作被画成橙色并弹二次确认，
    **系统在对用户说假话**。这条测试让「漏登记」在改 `NODES` 那一刻就红。
    """
    known = _known_flags()
    bad = {n: sorted(set(s.flags) - known) for n, s in inv.NODES.items()
           if set(s.flags) - known}
    assert not bad, (
        f'这些节点的 flag 三张表里都没有：{bad}\n'
        f'→ 补进 `FREE_ENTRIES`（0 成本入口）/ `PAID_FLAGS`（挂在付费动作上）'
        f'/ `VALUED_FLAGS`（带值的参数），别只是不提它'
    )


def test_paid_flags_are_a_subset_of_the_nodes_flags():
    """`paid_flags` 是 `flags` 的子集——一个节点接受不了的 flag，谈不上让它变付费。

    两张表分家的具体形态：`paid_flags` 里写着 `--revise`、而 `flags` 里没有 ⇒
    `build_argv` 会当场拒收它，于是那条「能把这个节点变付费」的声明**永远不可能被触发**，
    却一直挂在表里让人以为它管着事。
    """
    bad = {n: sorted(set(s.paid_flags) - set(s.flags)) for n, s in inv.NODES.items()
           if set(s.paid_flags) - set(s.flags)}
    assert not bad, f'这些节点的 paid_flags 不在自己的 flags 里：{bad}'


def test_valued_flags_are_an_orthogonal_axis():
    """**「贵不贵」与「带不带值」是两个正交的轴**，重叠是对的，不是 bug。

    ⚠ 这条测试的真正用途是**挡住一次顺手的合并**：
    `test_the_three_flag_tables_are_disjoint` 读起来像「所有 flag 表两两不交」，
    照那个印象把 `VALUED_FLAGS` 也加进去，`--apply-edit` 当场红——
    而它同时是 0 成本入口、又带一个载荷路径，两件事都是真的。
    """
    assert '--apply-edit' in inv.FREE_ENTRIES and '--apply-edit' in inv.VALUED_FLAGS, \
        '`--apply-edit` 同时是 0 成本入口与带值参数——这一条塌了说明有人合并了两个轴'
    unknown = sorted(set(inv.VALUED_FLAGS) - _known_flags())
    assert not unknown, f'VALUED_FLAGS 里有三张表都不认识的 flag：{unknown}'


def test_write_effects_leaves_out_the_flags_that_differ_across_nodes():
    """`WRITE_EFFECTS` 按 **flag** 索引，所以只许收**跨节点一致**的那些。

    ⚠ `--identity` 刻意不在表里，那不是漏登记：它在 stage7 只 `print`（不写盘），
    在 stage6 却把 `baselines_identity` **落进产物**。
    收进一张按 flag 索引的表，就必然对其中一层说假话；
    不收 ⇒ 落到「未知按会写算」⇒ 对 stage6 是对的、对 stage7 是保守的，两边都不说假话。
    """
    assert '--identity' not in inv.WRITE_EFFECTS, (
        '`--identity` 跨节点不一致（stage6 写盘、stage7 只 print），'
        '不许进一张按 flag 索引的表'
    )
    unknown = sorted(set(inv.WRITE_EFFECTS) - _known_flags())
    assert not unknown, f'WRITE_EFFECTS 里有三张表都不认识的 flag：{unknown}'
    # 「不写盘」那几条必须真的是 0 成本入口——一条会花钱的路不可能什么都不产。
    for flag, effect in inv.WRITE_EFFECTS.items():
        if not effect:
            assert flag in inv.FREE_ENTRIES, f'{flag} 声称不写盘，却不是 0 成本入口'


def test_partial_rewrite_flags_are_free_and_exclude_the_promotion_path():
    """局部改写的取值面 ⊆ `FREE_ENTRIES`，且**不含 `--revalidate`**。

    一条会花钱的路不叫「局部改写」，那是一次新的产出，该记一份新的来源账。
    而 `--revalidate` 改的是**状态**（`.INVALID` → 正式稿），内容一个字没动——
    它有自己的键 `revalidated`。两件事合并，报告就说不清
    「这份稿被人改过」与「这份稿只是换了个位置」。
    """
    extra = sorted(set(inv.PARTIAL_REWRITE_FLAGS) - set(inv.FREE_ENTRIES))
    assert not extra, f'这些不是 0 成本入口，不该算局部改写：{extra}'
    assert '--revalidate' not in inv.PARTIAL_REWRITE_FLAGS
    assert '--apply-edit' in inv.PARTIAL_REWRITE_FLAGS, (
        'stage7 只改人工映射 patch、保留模型人物表，是混版；'
        '不登记会让它的正式 0 成本修复路每次都在来源账校验处抛异常')


def test_every_captured_action_maps_onto_a_node_and_its_flags():
    """⚠ **这条是 `test_free_entries_covers_every_free_flag_the_live_backend_uses` 的姊妹条。**

    那一条查「这个 flag 有没有登记」，本条查「它挂在**对的节点**上没有」。
    两者差一个真实的故障：一个登记齐全但挂错节点的 flag，`is_paid` 照样答得出贵贱，
    而 `build_argv` 会把一条现役后端天天在跑的命令**当场拒收**。

    ⚠ 抓包里的 36 条 `actions` 是**现役后端真的会执行的那些**，不是示意图。
    ⚠ 翻译表在 `tests/legacy_names.py`，**两个测试文件共用一份**：
    `tests/test_api_enums.py` 拿同一张表做贵贱对账，
    各抄一份的分家表现是「贵贱对账假绿」（那张表的 docstring 记着实测的 2 条红）。
    """
    actions = legacy.captured_enums()['actions']
    assert len(actions) > 30, f'只抓到 {len(actions)} 条 action——样本换形状了，本条要跟着改'
    bad = []
    for a in actions:
        node, _mode, flags = legacy.translate(a)
        missing = sorted(set(flags) - set(inv.NODES[node].flags))
        if missing:
            bad.append(f'{a["kind"]}{a["extra"] or []} → {node} 缺 {missing}')
    assert not bad, (
        '现役后端跑得动、而新节点表接不住的跑法：\n  ' + '\n  '.join(bad)
        + '\n→ 要么补进那个节点的 `flags`，要么在 `SUPERSEDED_FREE_ENTRIES` 里'
          '说清它被什么取代了。**不许只是不提它**'
    )
