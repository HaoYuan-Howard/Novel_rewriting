#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naming.py —— 人物**专名**表。只在 stage3 的**校验期**用。

⚠ **抽取本身不吃 stage2**（风格与情节正交）。这份名表只服务
「专名泄漏」那条确定性比对——靠正则猜中文人名不可靠，所以借已抽好的名表。

⚠ 本模块**不读盘**：它接收已经由 `store.read` 读上来的 `GlobalGrounding` 模型。
原型这里是裸 `json.load`，于是 `style_grounding` 的 `inputs` 是空的
——**三份上游一个都没记**。走唯一入口，来源账才记得上。
"""
from __future__ import annotations

import re

from contracts.schema.stage2 import GlobalGrounding

#: 亲属/关系称谓（**语言层通用，非某本书特有**）：这类词面即使被登记为某角色的别名，
#: 出现在风格描述里通常是在说「怎么称呼」这条**可迁移**规则，不算专名泄漏。
KINSHIP = re.compile(r'^(我|你|他|她|前|亲|干|继|老|小)?'
                     r'(爹|娘|爸|妈|父|母|公|婆|夫|妻|君|兄|弟|姐|妹|嫂|舅|姨|叔|伯|婶|爷|奶|媳|婿|子|女|孙)')


def names_of(gg: GlobalGrounding) -> frozenset[str]:
    """全书事实底座 → 人物**专名**集合。

    名表来源 = `canonical` + `address[].surface`（「谁怎么称呼他」的字面；
    旧契约的 `aliases`/`titles` 已并入 `address`——头衔与别名本就是同一件事的两面）。

    **两层过滤**，滤掉「通用称谓」只留真专名（都是语言/schema 层的通用规则，非逐本 hint）：
      · 排除 `name_distinctiveness == 'common'` 的角色
        （stage2 对该档的定义就是「纯亲属词或多实体共享词」）；
      · 排除亲属/关系称谓词面（我娘、公爹、前夫君…）——它们常被存成高辨识角色的称呼。

    ⚠ 长度 ≥2：单字词面拿去扫描全篇描述必然满屏假阳。
    """
    return frozenset(
        s for c in gg.entities.characters.values()
        if c.name_distinctiveness != 'common'
        for s in ([c.canonical] + [a.surface for a in c.address])
        if s and len(s) >= 2 and not KINSHIP.match(s))


def spoiler_names_of(gg: GlobalGrounding, names: frozenset[str]) -> frozenset[str]:
    """`names` 里哪些同时是【读者还没被揭示的秘密】的主体。**只用来给告警分轻重。**

    专名泄漏本来就违约（描述换本书就不成立），但两种泄漏的严重程度差得很远：
      · 「许言」出现在 `voice` 里 —— 不可迁移，是个坏描述；
      · 「系统」出现在 `voice` 里 —— **同时是剧透**：它是 `secret_03` 的主体、
        读者 ch14 才知情，而 stage3 的产物会**整份进写手 prompt**，
        于是写 ch1 的**闭卷**写手当场读到全书最大的那个底（F-104/F-118）。

    ⚠ 实测两本：专名泄漏共 1 处，而那一处正好是剧透主体——**样本只有 1，
    别据此下「泄漏 ⊂ 剧透」的结论**。这个标注的用途是让人一眼看出该不该立刻重跑，
    **不是一条判据**（判据 32：名字不要抢跑）。

    没有 secrets（如書1）时返回空集——那是「这本书没有可查的秘密」，**不是「没有剧透」**。
    """
    if not names:
        return frozenset()
    out: set[str] = set()
    for s in gg.canon.secrets:
        rev = [e.chapter for e in s.reader_reveal_events if isinstance(e.chapter, int)]
        # 揭示章 > 1 才算「读者还没知道」——第 1 章就揭示的秘密写进 voice 不是剧透。
        if rev and min(rev) <= 1:
            continue
        out |= {n for n in names if n in s.fact}
    return frozenset(out)
