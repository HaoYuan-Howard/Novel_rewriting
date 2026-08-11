#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
legacy_names.py —— 原型作业名 → 两层命名。**只给对账测试用，不是契约。**

【为什么它住在 `tests/` 而不是 `contracts/`】`n1`~`n9` 整套已经退役
（`contracts/invariants.py` §九 的裁决记录）。把这张表放进 `invariants`，
等于把一批**死掉的名字**复活成一份现役取值面——而那个模块自称是唯一落点。
它在这里的唯一用途是：拿**现役后端的真实抓包**
（`docs/design_brief/05_samples/enums.json` 的 36 条 `actions`）回归新表。

【为什么它是一个模块而不是两个测试文件各抄一份】
`tests/test_invariants.py` 与 `tests/test_api_enums.py` 都要用它。
复制一份必然分家，而**这一次分家的表现是「贵贱对账假绿」**：
两边各有一张翻译表，其中一张漏了 mode 或漏了目标节点，
`is_paid` 照样答得出一个贵贱，只是答的是另一条跑法的。

⚠⚠ **本表在 2026-08-11（骨架 E1）被加宽过两处，两处都不是笔误而是真的漏**：

  ① `n5_write` / `n6_write` **原来都映到 `('stage5.write', ())`**——**丢了 mode**。
     两条臂在对账里合成了一条，于是「深度臂与浅层臂各自贵不贵」这件事从没被查过。
  ② `--context-only` / `--assemble` **原来映到 `None`**，只说「它变成了一个节点」
     而不说是**哪个**节点。拿它做贵贱对账，`n4_plan --context-only` 会被算成
     `stage4.plan` 的**付费默认路径**——而它其实是 0 成本的 `stage4.context` 节点。
     实测：加宽之前这条对账**红 2 条**，加宽之后 36/36 全绿。
"""
from __future__ import annotations

import json
import os

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'docs', 'design_brief', '05_samples')


def captured_enums() -> dict:
    """现役原型后端的真实抓包。**这份文件永远不许用新后端的输出覆盖**——

    覆盖之后 `test_enum_matches_live_backend` 那一族就变成拿 `invariants`
    跟它自己比，而那正是「有测试」与「测试在查东西」的分界线。
    """
    with open(os.path.join(SAMPLES, 'enums.json'), encoding='utf-8') as f:
        return json.load(f)


#: 原型 kind → `(节点, mode, 这个 kind 本身隐含的 flags)`。
#:
#: ⚠ 「隐含的 flags」那一格记的是**原型把一个 flag 做成了独立 kind** 的那几个：
#: `check_partition` / `check_blueprints` 是独立脚本，而两层命名下**没有 check 节点**，
#: 复检一律是 `--check-only`（`Q-3` 点名的「三个节点行为不一致」）。
KIND_TO_NODE: dict[str, tuple[str, str | None, tuple[str, ...]]] = {
    'n1_partition': ('stage1.partition', None, ()),
    'check_partition': ('stage1.partition', None, ('--check-only',)),
    'n2_global': ('stage2.global', None, ()),
    'n2_blueprint': ('stage2.blueprint', None, ()),
    'check_blueprints': ('stage2.blueprint', None, ('--check-only',)),
    'n3_style': ('stage3.style', None, ()),
    'n4_plan': ('stage4.plan', None, ()),
    # ⚠ 臂是**参数**不是节点，而 mode 那一格正是本表 2026-08-11 补上的那一半。
    'n5_write': ('stage5.write', 'deep', ()),
    'n6_write': ('stage5.write', 'shallow', ()),
    'stage5_review': ('stage5.review', None, ()),
    'n8_hook': ('stage6.hook', None, ()),
    'n9_rename': ('stage7.rename', None, ()),
}

#: 抓包里的 flag → 它在两层命名下变成了什么。
#: `('flag', x)` = 换了个名字；`('node', x)` = 它**本身**成了一个节点。
FLAG_REDIRECT: dict[str, tuple[str, str | None]] = {
    # `Q-3`「同一个语义在不同节点上必须叫同一个名字」的实例：
    # 它与 `--identity` 是同一件事，只因为原型里一个脚本干两件事才被迫改名。
    '--identity-plan': ('flag', '--identity'),
    '--context-only': ('node', 'stage4.context'),
    '--assemble': ('node', 'stage7.assemble'),
    # ⚠ `--approve` 去哪个节点**取决于 kind**（stage0 / stage6 / stage7 各有一个），
    # 而它在这 36 条抓包里**一次都没出现过** ⇒ 今天没有落点可以验。
    # 用 `None` 而不是随便挑一个：`translate()` 撞上它会当场抛，
    # 于是哪天样本里真的出现了 `--approve`，我们**被迫**在那一刻裁决它去哪儿，
    # 而不是让一条猜出来的映射静静地绿。
    '--approve': ('node', None),
}


def translate(action: dict) -> tuple[str, str | None, tuple[str, ...]]:
    """抓包的一条 `action` → `(节点, mode, flags)`。**认不出就抛，不猜。**"""
    kind = action['kind']
    if kind not in KIND_TO_NODE:
        raise KeyError(
            f'抓包里出现了本表不认识的 kind={kind!r}——样本换形状了，'
            f'要么补进 `KIND_TO_NODE`，要么说清它被什么取代了。**不许只是不提它**')
    node, mode, flags = KIND_TO_NODE[kind]
    out = list(flags)
    for f in (action.get('extra') or []):
        kind_of, target = FLAG_REDIRECT.get(f, ('flag', f))
        if target is None:
            raise KeyError(
                f'{kind}{action.get("extra")} 里的 {f!r} 在两层命名下去哪个节点，'
                f'今天没有裁决（见 `FLAG_REDIRECT` 里那条注释）。'
                f'抓包里第一次出现它 ⇒ 现在就得定，别让对账绕过去')
        if kind_of == 'node':
            node, mode = target, None
        else:
            out.append(target)
    return node, mode, tuple(out)
