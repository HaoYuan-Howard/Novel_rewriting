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
