#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naming.py —— 人物**专名**表，以及「有没有编出新人名」那条探针。

三组东西方向各不相同，别混：
  · `KINSHIP` —— Stage5 发明检测复用的通用称谓过滤；
  · `name_stems` / `invented_names` —— stage5/stage6 的**发明检测**（拿本书已有的姓
    去造新人，那是闭卷写手最容易干的一件事）；
  · `attested_surname` / `family_groups` / `is_generic_appellation` —— stage7 改名的
    **姓氏族群**（这个人姓什么、和谁一家、哪些称呼压根不该动）。

⚠ 旧第四组「叫法时点下发闸门」（`person_naming_surfaces` / `surface_first_chapter` /
`rule_names_a_person`）随 Stage2 语义审阅退役删除（2026-09）：现役 Characters v2
契约明确「`identity_revealed_from` 不表示称谓首次能出现的章，不能据此禁用称谓」，
章内称谓出现与表面身份的选择由原文和 Blueprint 支持。

⚠ 两组各有一份「亲属称谓」的正则（`KINSHIP` / `_STEM_KINSHIP`），**取值面刻意不同**，
合成一份会让其中一侧悄悄放宽——每一处的理由写在它自己旁边。

⚠ 本模块**不读盘**：它接收已经由 `store.read` 读上来的模型 / 已读上来的 dict。
原型这里曾裸读产物，退役 Stage3 v1 因而漏记上游；现役 consumer 均须走 store 的唯一入口。
"""
from __future__ import annotations

import re

from novel.core.text import norm

#: 亲属/关系称谓（**语言层通用，非某本书特有**）：这类词面即使被登记为某角色的别名，
#: 出现在风格描述里通常是在说「怎么称呼」这条**可迁移**规则，不算专名泄漏。
KINSHIP = re.compile(r'^(我|你|他|她|前|亲|干|继|老|小)?'
                     r'(爹|娘|爸|妈|父|母|公|婆|夫|妻|君|兄|弟|姐|妹|嫂|舅|姨|叔|伯|婶|爷|奶|媳|婿|子|女|孙)')


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

#: 改名时不能被当作普通字面换掉的关系、辈分与官职标记；同一份词面还供
#: `attested_surname` 按**整串相等**排除纯称谓/位号。最长优先只影响可读顺序，
#: 返回值仍是集合式的稳定元组；新增或丢失任何一项都会触发不变量。
_RENAME_SEMANTIC_TAILS = tuple(sorted({
    '外祖父', '外祖母', '祖父', '祖母', '父亲', '母亲', '丈夫', '妻子', '夫君', '娘子',
    '兄长', '长兄', '长姐', '哥哥', '姐姐', '弟弟', '妹妹', '嫂嫂', '嫂子', '舅舅',
    '姨母', '叔父', '伯父', '婶娘', '爷爷', '奶奶', '儿子', '女儿', '孙子', '孙女',
    '皇太女', '皇贵夫', '太子', '皇子', '世子', '公主', '郡主', '王妃', '皇后',
    '贵妃', '皇帝', '陛下',
    '殿下', '国公', '侯爷', '王爷', '将军', '丞相', '宰相', '尚书', '侍郎', '御史',
    '太傅', '太师', '知府', '县令', '大人', '公子', '小姐', '姑娘', '夫人', '娘娘',
    '嬷嬷',
}, key=lambda value: (-len(value), value)))
_RENAME_NUMERAL = re.compile(r'(?:第)?(?:\d+|[零〇一二两三四五六七八九十百]+)')
_RANK_PARTS = re.compile(
    r'^(?:第)?(?P<rank>大|[一二两三四五六七八九十百\d]+)'
    r'(?P<role>郎|娘|爷|哥|姐|弟|妹|公子|小姐)$')


def strip_distinguisher(canonical: str | None) -> str:
    """去掉 `canonical` 末尾的消重区分词。**两处共用这一份**（派生显示名 / 反查称呼），
    各写一份正则就是下一次「这边按 A 剥、那边按 B 剥」的来源。"""
    return _DISTINGUISHER.sub('', canonical or '').strip()


def registered_surfaces(character: dict | None) -> tuple[str, ...]:
    """当前注册表的全部有据表面称谓；不推断首次出现，也不展开同人披露图。"""
    return tuple(row['surface'] for row in (character or {}).get('aliases', [])
                 if isinstance(row, dict) and isinstance(row.get('surface'), str) and row['surface'])


def display_names(characters: dict[str, dict] | None, book_text: str
                  ) -> tuple[dict[str, str], dict[str, str], list[tuple[str, object]]]:
    """从当前人物表派生全书有据称呼；不给自造 registry 标签兜底。"""

    display: dict[str, str] = {}
    substitutions: dict[str, str] = {}
    unattested: list[tuple[str, object]] = []
    for char_id, character in (characters or {}).items():
        raw = (character or {}).get('canonical') or ''
        canonical = strip_distinguisher(raw)
        if canonical and canonical in book_text:
            display[char_id] = canonical
            continue
        surfaces = set(registered_surfaces(character))
        best = max(
            ((book_text.count(surface), len(surface), surface)
             for surface in surfaces if surface),
            default=(0, 0, ''),
        )
        if best[0] > 0:
            display[char_id] = best[2]
            for form in (raw, canonical):
                if form and form != best[2]:
                    substitutions[form] = best[2]
        else:
            display[char_id] = canonical or char_id
            unattested.append((char_id, raw))
    return display, substitutions, unattested


def project_character_names(
    characters: dict[str, dict] | None,
    book_text: str,
) -> tuple[dict[str, dict[str, object]], dict[str, str], list[tuple[str, object]]]:
    """Stage4/5 共用的全书名称检测基线。

    `display_name` 与 `name_distinctiveness` 从同一份当前人物表一起投影；
    Stage4 用显示名组装逐章任务，Stage5 用两者建立残留名字探针。拆成两套
    组装口，会让“写手收到的名字”和“验收认得的名字”悄悄分叉。
    """
    display, substitutions, unattested = display_names(characters, book_text)
    projected = {
        char_id: {
            'display_name': name,
            'name_distinctiveness': ((characters or {}).get(char_id) or {}).get(
                'name_distinctiveness'),
        }
        for char_id, name in display.items()
    }
    return projected, substitutions, unattested


def display_names_at(characters: dict[str, dict] | None, text_upto: str) -> dict[str, str]:
    """全书投影的章级版本；本章尚无有据称呼的人不进入结果。"""

    display: dict[str, str] = {}
    for char_id, character in (characters or {}).items():
        canonical = strip_distinguisher((character or {}).get('canonical') or '')
        if canonical and canonical in text_upto:
            display[char_id] = canonical
            continue
        surfaces = set(registered_surfaces(character))
        best = max(
            ((text_upto.count(surface), len(surface), surface)
             for surface in surfaces if surface),
            default=(0, 0, ''),
        )
        if best[0] > 0:
            display[char_id] = best[2]
    return display


def name_collisions(display: dict[str, str], characters: dict[str, dict] | None
                    ) -> list[tuple[str, list[str], list[object]]]:
    """返回派生称呼撞名及其原始 canonical，供调用方显式报告。"""

    seen: dict[str, list[str]] = {}
    for char_id, name in (display or {}).items():
        seen.setdefault(name, []).append(char_id)
    return [
        (name, ids, [((characters or {}).get(char_id) or {}).get('canonical')
                     for char_id in ids])
        for name, ids in seen.items() if len(ids) > 1
    ]


def rename_semantic_markers(surface: str | None) -> tuple[str, ...]:
    """改名前后必须保持的数字、排行、辈分、关系与官职标记。

    这不是名字相似度：`阿九 → 玄九` 合格、`阿九 → 阿七` 不合格；
    `李将军 → 沈将军` 合格、`李将军 → 沈凌` 不合格。比较完整 marker 元组，
    因而偷偷新增一个职位同样会出声。
    """
    value = strip_distinguisher(surface)
    markers = {f'number:{m.group().removeprefix("第")}'
               for m in _RENAME_NUMERAL.finditer(value)}
    if rank := _RANK_PARTS.fullmatch(value):
        markers.add(f'rank-role:{rank.group("role")}')
        # `大郎` 的「大」也是排行；普通 numeral 正则不收它，显式补在这里。
        markers.add(f'rank-value:{rank.group("rank")}')
    for tail in _RENAME_SEMANTIC_TAILS:
        if value.endswith(tail):
            markers.add(f'role:{tail}')
            break
    return tuple(sorted(markers))


def surface_owners(characters: dict[str, dict] | None) -> dict[str, frozenset[str]]:
    """`{称呼字面: {char_id…}}` —— 一个称呼可能挂着好几个人（「姐姐」「书童」）。

    字面取 `canonical`（**剥掉区分词**）+ 全部 `address[].surface`。

    ⚠ 键一律走 `core.text.norm`（内容字符）：原文的全半角标点与「每句一段」的换行
    都不可靠，拿原始字节做键会让「祈颜！」和「祈颜」变成两个不同的称呼。
    """
    owners: dict[str, set[str]] = {}
    for cid, v in (characters or {}).items():
        names = registered_surfaces(v)
        for nm in names:
            key = norm(nm)
            if key:
                owners.setdefault(key, set()).add(cid)
    return {k: frozenset(v) for k, v in owners.items()}


def attested_surname(char: dict | None, book_text: str,
                     owners: dict[str, frozenset[str]] | None = None) -> str | None:
    """这个角色的**姓**（原文有据时 `canonical` 的首字），否则 `None`。五道过滤：

    ⓪ **`name_distinctiveness == common` 不是专名。** `太后` / `太傅` 都有据、
       都是两字且各自唯一；若忽略 Stage 2 已有的分类，它们会因同为「太」开头而被
       错组为一族，模型随即交出 `太→宋` 这种幽灵姓氏映射。
    ① **`canonical` 必须在原文里有据。** registry 里有一批**自造的记账标签**
       （实测 `书童(陆明洲)` / `许言的姐姐` / `谈默母亲` / `苏父`），它们的首字不是姓：
       「书」不是姓；「许言的姐姐」的「许」是**别人**的姓，碰巧对上纯属巧合。
    ② **长度 2~3**：4 字以上基本是标签不是姓名。
    ③ **完整 `canonical` 不能就是纯关系/官职/位号。** `皇贵夫` / `皇太女`
       即使被 Stage 2 保守记成 medium，也不是姓「皇」的两个人。这里只做**整串相等**；
       `王夫人` / `李将军` 只是以角色词结尾，首字仍是真姓。
    ④ **`canonical` 不能挂着多个 `char_id`**：那说明它是个通用角色词而不是名字。
       实测「书童」同时是三个角色的称呼——它有 18 次出现、完全「有据」，
       ①②两道都拦不住，**只有这一道拦得住**。

    ⚠ 五道加起来仍然不完美：`采草贼`/`府医`/`圣上`/`老御医` 都能过（3 字、有据、单主），
    于是「采」「府」「圣」「老」会被当成姓。**这是靠 `family_groups` 的「≥2 人才成族」
    兜住的**——它们全是单例，而单例根本不需要跟谁协调姓氏。
    别在这里再造一张逐本角色词表：纯位号只复用改名语义标记的唯一语言层词表，
    而且只按整串相等排除；用 `endswith` 会把 `王夫人` / `李将军` 的真姓一起删掉。

    ⚠⚠ **这些过滤会漏掉真姓，而且是必然的**——实测某本两例：`沈` 只由自造记账标签
    `沈母`/`沈父` 承载（无原文证据时判掉）· `孙` 撞上亲属字表的「孙大娘」
    （亲属词过滤判掉）。
    **别为此放宽这里**：本函数的产物是**下发面**，放宽等于拿假姓去让模型协调族群
    （收窄一个下发面是修复，收窄一个检测器才是削弱）。
    漏掉的真姓由**探针侧**兜住——`stems` 并上模型的 `surname_map`（`R-10.6` / `R-10.6b`）。
    """
    if (char or {}).get('name_distinctiveness') == 'common':
        return None
    canon = strip_distinguisher((char or {}).get('canonical') or '')
    if not (2 <= len(canon) <= 3) or canon not in (book_text or ''):
        return None
    if _STEM_KINSHIP.match(canon):              # 「婆母」「表哥」「堂姐」：首字不是姓
        return None
    if _DESCRIPTIVE_ROLE_PREFIX.match(canon):   # 「胖媒婆」「瘦掌柜」：修饰语不是姓
        return None
    if canon in _RENAME_SEMANTIC_TAILS:         # 「皇贵夫」「皇太女」整串是位号，不姓「皇」
        return None
    if owners and len(owners.get(norm(canon)) or ()) > 1:
        return None
    return canon[0]


def family_groups(characters: dict[str, dict] | None, book_text: str,
                  owners: dict[str, frozenset[str]] | None = None
                  ) -> tuple[dict[int, list[str]], dict[str, str | None], dict]:
    """姓氏族群 → `({组号: [char_id…]}, {char_id: 姓}, 记账)`。

    **判据只有一条：经 `attested_surname` 排除无据标签与完整纯称谓后，
    `canonical` 字面同姓，且这个姓至少挂着两个人。**

    ⚠ **原来还有一条「关系里的血亲/姻亲边」，已经删掉——它从未独立贡献过，
    只带来错误合并。** 逐条复算：
      · 某本的姐妹**本来就字面同姓**，字面那条已经抓到；
      · 某本的兄弟在关系里**根本没有边**，只有字面那条抓得到；
      · 而谓词词表（含 夫/妻/兄/弟…）实测把「是妻子」与「是结拜兄弟」都算成合并边，
        于是三个姓被并成一族——给这一族一个新姓，就成了**夫妻同姓、结拜兄弟同姓**，
        两样都错。
    ⇒ 一条**只会造成错误合并、从未独立抓到过东西**的边，删掉是化简不是删功能。

    **为什么要求「≥2 人」**：一个人自己不需要跟谁协调姓氏。这一条同时兜住了
    `attested_surname` 剩余保守过滤没能识别的假姓（当前实测都是单例）。完整纯位号
    曾出现两人同首字，不能靠这条兜，已在 `attested_surname` 按整串语义标记排除。
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
