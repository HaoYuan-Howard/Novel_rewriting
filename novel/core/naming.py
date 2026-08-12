#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naming.py —— 人物**专名**表，以及「有没有编出新人名」那条探针。

四组东西方向各不相同，别混：
  · `names_of` / `spoiler_names_of` —— stage3 校验期的**专名表**（这些名字不许出现在
    可迁移的风格描述里）；
  · `name_stems` / `invented_names` —— stage5/stage6 的**发明检测**（拿本书已有的姓
    去造新人，那是闭卷写手最容易干的一件事）；
  · `attested_surname` / `family_groups` / `is_generic_appellation` —— stage7 改名的
    **姓氏族群**（这个人姓什么、和谁一家、哪些称呼压根不该动）；
  · `person_naming_surfaces` / `surface_first_chapter` / `rule_names_a_person` ——
    stage4 的**叫法时点**（这条规则点到了谁、读者第几章才叫得出这个叫法）。

⚠ 第四组与第一组的方向**正相反**，别拿一组去代另一组：那边问「这个名字在不在名表里」，
这边问「**读者到这一章为止，叫不叫得出这个叫法**」——同一批数据，一个是集合、一个是时点。

⚠ 三组各有一份「亲属称谓」的正则（`KINSHIP` / `_STEM_KINSHIP`），**取值面刻意不同**，
合成一份会让其中一侧悄悄放宽——每一处的理由写在它自己旁边。

⚠ **抽取本身不吃 stage2**（风格与情节正交）。第一组名表只服务
「专名泄漏」那条确定性比对——靠正则猜中文人名不可靠，所以借已抽好的名表。

⚠ 本模块**不读盘**：它接收已经由 `store.read` 读上来的模型 / 已读上来的 dict。
原型这里是裸 `json.load`，于是 `style_grounding` 的 `inputs` 是空的
——**三份上游一个都没记**。走唯一入口，来源账才记得上。
"""
from __future__ import annotations

import re

from contracts.schema.stage2 import GlobalGrounding
from novel.core.text import norm

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


# ─────────────────────────────────────────────────────────────────────────────
# 姓氏族群 —— stage7 改名那一族。**它与上面两组又是不同的方向**：
# 上面查「名字有没有泄漏 / 有没有被编出来」，这里答「这个人姓什么、和谁一家」。
# ─────────────────────────────────────────────────────────────────────────────

#: `canonical` 末尾的**消重区分词**：`李嬷嬷(慧贵妃宫)` → `李嬷嬷`。
#: 区分词是为了满足「registry 键全书唯一」加的，**不是这本书的语言**——
#: 原文里没人管谁叫「小宫女(打翻茶盏)」。不剥就等于让所有带区分词的角色永远解析不到。
_DISTINGUISHER = re.compile(r'[（(][^）)]*[）)]\s*$')

#: 「胖媒婆」「瘦掌柜」——外貌修饰词不是姓。
#: ⚠ 实跑抓到过：`胖媒婆`/`胖婶` 被按首字并成一族，模型只好产出「庞媒婆/庞婶」。
_DESCRIPTIVE_ROLE_PREFIX = re.compile(r'^[胖瘦]')

#: 称谓/亲属后缀落在**词尾**（`许总` / `李嬷嬷`）——`NAME_TITLE` 的锚定版。
_TITLE_TAIL = re.compile(NAME_TITLE + r'$')

#: 排行式称呼：数字是**人物关系/排行**，不是名字的一部分。
#: ⚠ 实跑抓到过：模型把「七郎」改成「六郎」，后缀还在（后缀那条判据全绿），
#: 而人物排行已经被改写。
_RANK_APPELLATION = re.compile(
    r'^(?:第)?(?:大|[一二两三四五六七八九十百\d]+)(?:郎|娘|爷|哥|姐|弟|妹|公子|小姐)$')


def strip_distinguisher(canonical: str | None) -> str:
    """去掉 `canonical` 末尾的消重区分词。**两处共用这一份**（派生显示名 / 反查称呼），
    各写一份正则就是下一次「这边按 A 剥、那边按 B 剥」的来源。"""
    return _DISTINGUISHER.sub('', canonical or '').strip()


def surface_owners(characters: dict[str, dict] | None) -> dict[str, frozenset[str]]:
    """`{称呼字面: {char_id…}}` —— 一个称呼可能挂着好几个人（「姐姐」「书童」）。

    字面取 `canonical`（**剥掉区分词**）+ 全部 `address[].surface`。

    ⚠ 键一律走 `core.text.norm`（内容字符）：原文的全半角标点与「每句一段」的换行
    都不可靠，拿原始字节做键会让「祈颜！」和「祈颜」变成两个不同的称呼。
    """
    owners: dict[str, set[str]] = {}
    for cid, v in (characters or {}).items():
        names = [strip_distinguisher((v or {}).get('canonical') or '')]
        names += [(a or {}).get('surface', '').strip()
                  for a in ((v or {}).get('address') or [])]
        for nm in names:
            key = norm(nm)
            if key:
                owners.setdefault(key, set()).add(cid)
    return {k: frozenset(v) for k, v in owners.items()}


def person_naming_surfaces(characters: dict[str, dict] | None) -> dict[str, str]:
    """`{叫法: char_id}` —— 只留【能标识某个具体的人】的那些叫法。

    `surface_owners` 收的是「谁被这么叫过」的**全集**，拿它直接当人名探测器会**误杀真规则**。
    两道筛，各修掉一类实测出来的错（三本书 5 条规则上全部判对）：

      ① **通名不算**（`name_distinctiveness == 'common'`）——「正室」「淫贼」「采花贼」
         都被登记成了角色，于是「纳妾制度：正室高于妾室」「淫贼会被官府追捕」这类
         **纯世界规则**被当成情节整条剔掉。
      ② **一个叫法挂着不止一个人时，它标识不了任何人**——「皇帝」同时是两个 char_id
         的叫法，于是「皇帝有权赐婚」会被误杀。这不是为样本硬凑的第三条规则，
         它就是「歧义 ⇒ 不猜」那条不变量换个方向用。

    ⚠⚠ **为什么必须收 `address[].surface` 而不只是 `canonical`**：实测某本
    `wr_06`「怡红院…**实为百晓生所有**…**龟公等人员可能是卧底**」——那正是
    `secret_01`（揭示章 ch8）的全部内容，而那个角色的 `canonical` 是「谢允」，
    规则正文里一次都没出现。于是这条规则逐字发给了 ch1~ch7 的写手，
    **同一份上下文里 `must_not_reveal` 还写着 `secret_01`**。
    **剧透是信息的属性，不是字段的属性**——探测器只认一个字段，信息就从另一个字段走。
    """
    out: dict[str, str] = {}
    for key, cids in surface_owners(characters).items():
        if len(key) < 2 or len(cids) != 1:            # ② 歧义的叫法不标识人
            continue
        cid = next(iter(cids))
        if ((characters or {}).get(cid) or {}).get('name_distinctiveness') == 'common':
            continue                                  # ① 通名不是人名
        out[key] = cid
    return out


def surface_first_chapter(characters: dict[str, dict] | None) -> dict[str, int]:
    """`{叫法: 读者最早能叫出它的章}` —— **与 `person_naming_surfaces` 同一套键**。

    值取该叫法自己的 `address[].from_chapter`（同一叫法多条时取最小）。
    只作为 `canonical` 出现、`address` 里没有的，退回该角色**最早**的 address 章：
    那种 canonical 多半是内部记账标签，拿它当时点没有意义。
    一条都取不到时给 1 —— **不知道就不扣**：扣错的代价是写手丢一条设定。

    ⚠ 键一律走 `core.text.norm`，与 `surface_owners` **同一个键空间**。
    第四组这三个函数是**串起来用的**（`naming` 的命中拿去查 `avail`），
    键空间差一格，闸门就会在某几个叫法上静默失效。
    """
    out: dict[str, int] = {}
    for _cid, v in (characters or {}).items():
        ads = [a for a in ((v or {}).get('address') or []) if (a.get('surface') or '').strip()]
        earliest = min((int(a.get('from_chapter') or 1) for a in ads), default=1)
        key0 = norm(strip_distinguisher((v or {}).get('canonical') or ''))
        if key0:
            out[key0] = min(out.get(key0, earliest), earliest)
        for a in ads:
            k = norm(a.get('surface') or '')
            if k:
                ch = int(a.get('from_chapter') or 1)
                out[k] = min(out[k], ch) if k in out else ch
    return out


def rule_names_a_person(desc: str | None, naming: dict[str, str]) -> list[str]:
    """这条规则的正文里点到了哪些【具体的人】→ 命中的叫法（有序去重，**长的在前**）。

    `naming` 由 `person_naming_surfaces` 给。长的在前是为了**报告可读**：
    同时命中「谢允」「谢大兄弟」时，先说长的那个更能说明它在讲谁。
    """
    hay = norm(desc)
    if not hay:
        return []
    return sorted({n for n in naming if n in hay}, key=lambda s: (-len(s), s))


def attested_surname(char: dict | None, book_text: str,
                     owners: dict[str, frozenset[str]] | None = None) -> str | None:
    """这个角色的**姓**（原文有据时 `canonical` 的首字），否则 `None`。三道过滤：

    ① **`canonical` 必须在原文里有据。** registry 里有一批**自造的记账标签**
       （实测 `书童(陆明洲)` / `许言的姐姐` / `谈默母亲` / `苏父`），它们的首字不是姓：
       「书」不是姓；「许言的姐姐」的「许」是**别人**的姓，碰巧对上纯属巧合。
    ② **长度 2~3**：4 字以上基本是标签不是姓名。
    ③ **`canonical` 不能挂着多个 `char_id`**：那说明它是个通用角色词而不是名字。
       实测「书童」同时是三个角色的称呼——它有 18 次出现、完全「有据」，
       ①②两道都拦不住，**只有这一道拦得住**。

    ⚠ 三道加起来仍然不完美：`采草贼`/`府医`/`圣上`/`老御医` 都能过（3 字、有据、单主），
    于是「采」「府」「圣」「老」会被当成姓。**这是靠 `family_groups` 的「≥2 人才成族」
    兜住的**——它们全是单例，而单例根本不需要跟谁协调姓氏。
    别在这里加一张角色词词表：那是判据 11 说的关键词表，换一本书立刻失效。

    ⚠⚠ **这三道过滤会漏掉真姓，而且是必然的**——实测某本两例：`沈` 只由自造记账标签
    `沈母`/`沈父` 承载（①判掉）· `孙` 撞上亲属字表的「孙大娘」（③判掉）。
    **别为此放宽这里**：本函数的产物是**下发面**，放宽等于拿假姓去让模型协调族群
    （收窄一个下发面是修复，收窄一个检测器才是削弱）。
    漏掉的真姓由**探针侧**兜住——`stems` 并上模型的 `surname_map`（`R-10.6` / `R-10.6b`）。
    """
    canon = strip_distinguisher((char or {}).get('canonical') or '')
    if not (2 <= len(canon) <= 3) or canon not in (book_text or ''):
        return None
    if _STEM_KINSHIP.match(canon):              # 「婆母」「表哥」「堂姐」：首字不是姓
        return None
    if _DESCRIPTIVE_ROLE_PREFIX.match(canon):   # 「胖媒婆」「瘦掌柜」：修饰语不是姓
        return None
    if owners and len(owners.get(norm(canon)) or ()) > 1:
        return None
    return canon[0]


def family_groups(characters: dict[str, dict] | None, book_text: str,
                  owners: dict[str, frozenset[str]] | None = None
                  ) -> tuple[dict[int, list[str]], dict[str, str | None], dict]:
    """姓氏族群 → `({组号: [char_id…]}, {char_id: 姓}, 记账)`。

    **判据只有一条：`canonical` 字面同姓，且这个姓至少挂着两个人。**

    ⚠ **原来还有一条「关系里的血亲/姻亲边」，已经删掉——它从未独立贡献过，
    只带来错误合并。** 逐条复算：
      · 某本的姐妹**本来就字面同姓**，字面那条已经抓到；
      · 某本的兄弟在关系里**根本没有边**，只有字面那条抓得到；
      · 而谓词词表（含 夫/妻/兄/弟…）实测把「是妻子」与「是结拜兄弟」都算成合并边，
        于是三个姓被并成一族——给这一族一个新姓，就成了**夫妻同姓、结拜兄弟同姓**，
        两样都错。
    ⇒ 一条**只会造成错误合并、从未独立抓到过东西**的边，删掉是化简不是删功能。

    **为什么要求「≥2 人」**：一个人自己不需要跟谁协调姓氏。这一条同时兜住了
    `attested_surname` 那三道过滤漏掉的假姓（全是单例）。
    ⚠ 单例**照样会被改名**，只是不进族群表——**别把「不在族群表」读成「不改名」**。

    ⚠ 字面同姓不等于同族（若某本书里有两个无关的王姓角色），但**换成同一个新姓仍然正确**：
    原作让他们同姓，成品也让他们同姓，这是保真，不是错误。
    """
    sur = {cid: attested_surname(v, book_text, owners)
           for cid, v in (characters or {}).items()}
    by_sur: dict[str, list[str]] = {}
    for cid, s in sorted(sur.items()):
        if s:
            by_sur.setdefault(s, []).append(cid)
    out: dict[int, list[str]] = {}
    singles: list[str] = []
    for i, (s, cids) in enumerate(sorted(by_sur.items()), 1):
        if len(cids) > 1:
            out[i] = sorted(cids)
        else:
            singles.append(s)
    return out, sur, {'families': len(out), 'singleton_surnames': sorted(singles)}


def is_generic_appellation(old: str | None, surnames: frozenset[str] | set[str] = frozenset()
                           ) -> bool:
    """这个旧称呼是【纯关系/敬称】（不该改名）还是【带姓的真名】（必须改）？

    【首字是**本书的姓**时，亲属字判定不成立】亲属字表里有 `孙`（孙子）/`公`/`子`/
    `女`/`母`——它们**同时是常见姓氏**。判据从前只问「开头是不是亲属字」，于是某本的
    `孙大娘` 命中、被判成「把关系当名字换掉了」。实测那本书：`孙` 在姓氏映射里、
    原文「孙大娘」6 次、「孙子」**0 次**——它就是**姓孙的大娘**，而下发的规则明写
    「带姓的称呼是真名，必须改」。
    **按这条判据去修数据，会让一个原作的姓氏活着进入「原创」成品**，那就是判据错。

    ⚠ 排除依据用**本书的姓氏映射**，不硬编码一张「哪些亲属字也是姓」的表：
    族群表是从本书 registry 算出来的证据，换一本书自动跟着变；
    在没把 `孙` 当姓的书里，`孙媳妇` 仍按亲属字拦。
    """
    if not old:
        return False
    if _RANK_APPELLATION.fullmatch(old):
        return True
    if _TITLE_TAIL.fullmatch(old):
        return True
    return bool(_STEM_KINSHIP.match(old)) and old[:1] not in set(surnames or ())
