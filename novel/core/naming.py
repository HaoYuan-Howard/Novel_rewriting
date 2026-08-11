#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naming.py —— 人物**专名**表，以及「有没有编出新人名」那条探针。

两组东西方向**正相反**，别混：
  · `names_of` / `spoiler_names_of` —— stage3 校验期的**专名表**（这些名字不许出现在
    可迁移的风格描述里）；
  · `name_stems` / `invented_names` —— stage5/stage6 的**发明检测**（拿本书已有的姓
    去造新人，那是闭卷写手最容易干的一件事）。

⚠ **抽取本身不吃 stage2**（风格与情节正交）。第一组名表只服务
「专名泄漏」那条确定性比对——靠正则猜中文人名不可靠，所以借已抽好的名表。

⚠ 本模块**不读盘**：它接收已经由 `store.read` 读上来的模型 / 已读上来的 dict。
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


# ─────────────────────────────────────────────────────────────────────────────
# 发明检测 —— 与上面方向相反：查「下发面里**没有**的人名」
# ─────────────────────────────────────────────────────────────────────────────

#: 亲属/关系称谓词面（我娘、公爹、前夫君…）——它们不是**名字用字**，不进 `name_stems` 的池子。
#: ⚠ 与上面的 `KINSHIP` 是同一判据的两侧、**取值面刻意不同**：
#: 那边滤「专名泄漏」的名表，这边滤「名字用字池」，所以这边多收了
#: `表/堂/兄/哥/嫂` 几个（堂兄不是名字用字，但它可以是一个被登记的称呼）。
#: 合成一份会让其中一侧悄悄放宽。
_STEM_KINSHIP = re.compile(
    r'^(我|你|他|她|前|亲|干|继|老|小|表|堂)?'
    r'(爹|娘|爸|妈|父|母|公|婆|夫|妻|君|兄|哥|弟|姐|妹|嫂|舅|姨|叔|伯|婶|爷|奶|媳|婿|子|女|孙)')

#: 【人物称谓后缀】——「专名」这一半的探针，形状与 `facts.NUM_UNIT` **完全相同**：
#: **词干 + 类别标记**。
#: ⚠ 只收**不作普通名词单用**的那些：「府/家/宫/城/街/山」看着像专名后缀，实际全是普通名词，
#: 原型实测拿它们做后缀，某本 23 章报出 82 条候选、几乎 100% 假阳
#: （「回头往街」「她的家」「召我入宫」）。根因是**中文没有词边界**：
#: 数字给了单位一个硬左锚，而这些标记没有。
NAME_TITLE = r'(?:嬷嬷|公子|小姐|娘娘|将军|夫人|王爷|姑娘|大人|氏)'


def name_stems(characters: dict[str, dict] | None) -> frozenset[str]:
    """本书的**名字用字池**——`invented_names` 的**左锚**。

    取人物表里真人名的首字（`name_distinctiveness` 是 high/medium，且不是亲属称谓）。
    ⚠ 姓氏池**得从书里长出来，不能写死**：写死一张百家姓就是判据 11 说的「关键词表」，
    换一本书（现代言情 / 西幻）立刻失效。

    ⚠⚠ **绝不收窄到「本章已出现的人」。** 人物表在**下发面**按章收窄（写手只该看到
    本章已存在的人），而**探针的名字池要用全书的**——同一份数据两处方向正相反：
    收窄下发面是为了不剧透，收窄名字池只会让探针漏掉真发明。
    """
    return frozenset(
        n[0] for c in (characters or {}).values()
        for n in [(c or {}).get('display_name') or '']
        if 2 <= len(n) <= 3 and (c or {}).get('name_distinctiveness') in ('high', 'medium')
        and not _STEM_KINSHIP.match(n))


def invented_names(body: str, given: str, stems: frozenset[str]) -> list[str]:
    """`body` 里出现、而 `given`（下发面）里没有的**人物称谓**。返回排序后的候选。

    左锚是**本书自己的名字用字**：没有左锚时，「位嬷嬷」（来自「两位嬷嬷」）
    「本小姐」「贵公子」这类词尾会全部命中。原型实测某本 23 章：
    无左锚 **82 条**候选、几乎全假；加上左锚 **0 条**，另一本同样 0 条。

    ⚠⚠ **召回是有缺口的，别把「报 0 条」读成「没编人名」**（判据 7）：
      · 用本书**没出现过的姓**造出来的人（「钱姑娘」）抓不到——那个字不在池子里；
      · **不带称谓后缀**的光名字（「小翠」）抓不到；
      · **地名 / 物名完全不查**（「府/家/宫/城」做后缀噪声压倒信号，见 `NAME_TITLE`）。
    它能抓住的是**拿本书已有的姓去造新人**（「苏二小姐」「江三公子」），
    而那正是闭卷写手最容易干的一件事：手边只有这几个姓。
    """
    if not stems:
        return []
    pat = re.compile('[' + ''.join(sorted(stems)) + r'][一-龥]{0,1}' + NAME_TITLE)
    return sorted({m.group() for m in pat.finditer(body)}
                  - {m.group() for m in pat.finditer(given)})
