#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
facts.py —— 中文正文里的**数值词法**与 secret 的**关键片段切法**。
**逐字移植自原型（`Stage2/stage2_common.py`），注释一起搬。**

两样东西都是「拿文本做字面匹配」的**唯一落点**，各有多个消费方：
  · `cn_num_pattern` / `NUM_UNIT` / `num_value` —— stage5 的数值守恒 · stage6 的「发明数字」探针；
  · `secret_keywords` —— stage4 的下发面闸门 · stage5 的提前点破 · stage6 的秘密记账。

各处自己抄一份切法必然漂，而漂的表现是**其中一处悄悄不再拦**（判据 25）。

2026-08-23 起还承载 secret 与 relation 的**时间线读法**（`first_reveal_chapter` /
`setup_chapters_of` / `relation_visibility_gates`）。它不是字面匹配，收进来是同一个
理由：归一、下发闸门与判据都要问同一条事实何时可见，不能各写一份判向。

relation 的显式 `visibility` 是生产权威；旧的人名 + 隐瞒窗口启发式保留在
`heuristic_relation_gates`，只给 INFO 对账，不能再决定实际下发。
"""
from __future__ import annotations

import re
from typing import Any

from novel.core.naming import strip_distinguisher
from novel.core.text import norm_index

CN_NUM_CHARS = '〇零一二两三四五六七八九十百千万'

#: 片段长度下限。⚠ **这个数没有独立标定过**——见 `secret_keywords` 的告诫。
SECRET_KEYWORD_MIN = 6


def cn_num_pattern(exclude_bare_one: bool = False) -> str:
    """中文正文里的数值 token 词法。**唯一落点。**

    小数必须作为**一个** token；`半` 与 `0.5` 是同一个值。原型此前把 `0.5` 拆成
    `0`/`5`，而成稿的「半个时辰」一个 token 都抽不到，导致值明明没变却被整章拦下。

    `exclude_bare_one` 只给「发明数字」探针用：单独的「一」在中文里大多是
    **不定冠词**，不该当作事实数字。
    """
    chinese = (rf'(?![一](?![十百千万]))[{CN_NUM_CHARS}]{{1,6}}'
               if exclude_bare_one else rf'[{CN_NUM_CHARS}]{{1,6}}')
    # 不许从一个更长数词的**中间**起配。`exclude_bare_one=True` 会跳过不定冠词「一」；
    # 原型的正则随后从「半」重新起配，于是「走到一半回了下头」被截成 `半回`，
    # 误报成事实性数字。左边界属于**数值 token 的词法**，不是某本书的 hint。
    left = rf'(?<![0-9.{CN_NUM_CHARS}半])'
    return left + rf'(?:[0-9]+(?:\.[0-9]+)?|半|{chinese})'


CN_NUM_RE = re.compile(cn_num_pattern())

#: 【只认带计量单位的数字】裸数字在中文里大量是成语与虚指（「四目相对」「十余士兵」
#: 「三言两语」），拿它当「编造的事实」报，噪声会把真信号淹掉（原型实测首轮 2/2 命中全是假阳）。
#: 带单位的才是**可核对的事实**：年岁/次数/成数/金额/距离…这是语言层规则，不是逐本 hint。
#: ⚠ 数词里**排除单独的「一」**：「一封密折」「这一日」说的是「某封」「某天」，
#: 不是事实数字——实测它贡献了全部剩余假阳。
#: ⚠ 单位只留**承载事实**的那些；纯量词（封/个/条/杯/道）不留，
#: 它们后面的数字几乎总是虚指。
NUM_UNIT = re.compile(cn_num_pattern(exclude_bare_one=True)
                      + r'(?=[年岁月成倍斤里品级重刻鞭板次回遍])')


# ── 「数值不得改动」查的是【值】不是【字面】────────────────────────────────────
#
# ⚠⚠ **这一族是被一次全量误报逼出来的。** 契约发给写手的原话是「数字必须保住…
# **措辞随你**——你可以换一种说法把它说出来，但那个数不能变」，而判据只在成稿里
# 搜阿拉伯字面：原型实测某章被 `('30岁', ['30'])` / `('已经8位数。', ['8'])`
# **整章拦下**，而成稿写的是「三十岁」「八位数」——**值一个都没变，
# 写手做的正是契约让它做的事**。古风/口语正文里把 30 写成「30」反而出戏，
# 所以照这条判据去修数据必然让产物更差（那正是「判据错了」的信号）。
# ⇒ 比对改成**值级**：两边都归一成数值再比。
_CN_DIGIT = {'〇': 0, '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
             '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_CN_UNIT = {'十': 10, '百': 100, '千': 1000, '万': 10000}


def num_value(tok: str | None) -> int | float | None:
    """`'30'` / `'三十'` / `'0.5'` / `'半'` → 数值；**解析不了返回 `None`（不猜）**。

    ⚠ 返回 `None` 与返回 `0` 必须分得开：前者是「这串不是数」，后者是「这串是零」。
    调用方拿它当比对键，猜一个值出来就会把两个不同的事实判成同一个。
    """
    s = (tok or '').strip()
    if not s:
        return None
    if s == '半':
        return 0.5
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', s):
        return float(s) if '.' in s else int(s)
    total, cur, seen = 0, 0, False
    for ch in s:
        if ch in _CN_DIGIT:
            cur, seen = _CN_DIGIT[ch], True
        elif ch in _CN_UNIT:
            u = _CN_UNIT[ch]
            if u == 10000:                       # 「万」结算前面所有位
                total = (total + (cur or 1)) * u
                cur = 0
            else:
                total += (cur or 1) * u
                cur = 0
            seen = True
        else:
            return None
    return (total + cur) if seen else None


def num_values(text: str | None) -> set[int | float]:
    """一段文字里所有**解析得出值**的数字 → 值的集合。解析不了的直接不进集合。"""
    return {v for tok in CN_NUM_RE.findall(text or '') if (v := num_value(tok)) is not None}


def num_unit_keys(text: str | None) -> dict[tuple[object, str], str]:
    """`{(值|字面, 单位): 展示串}` —— 「发明数字」探针的比对键。

    键按**值**取（`三十岁` 与 `30岁` 同键），展示串留正文里的**原样**——
    报出来的东西要是人在正文里找得到的那个写法，不是我们归一之后的样子。
    ⚠ 解析不出值时**用字面当键**（不丢弃）：那仍然是一个可核对的事实，
    只是我们换算不了它；丢掉它等于让一条查不了的事实变成一条「查过、没问题」。

    ⚠ `m.end()` 可能正好落在串尾（数字是最后一个字符），
    原型旧写法 `text[m.end()]` 在那里 `IndexError`。
    """
    t = text or ''
    out: dict[tuple[object, str], str] = {}
    for m in NUM_UNIT.finditer(t):
        unit = t[m.end()] if m.end() < len(t) else ''
        v = num_value(m.group())
        # 「成」既是成数单位，也是大量普通词的首字。正则只能看见
        # 「一百成功」里的「一百成」，但 >10 成不是合法成数；若不在共享
        # 抽取层剔除，stage6 会把「好感度一百成功」误报成编造数字。
        # 不能简单写 `成(?!功)`：那只补一个词，下一本书遇到「一百成就」
        # 还会重犯；按成数的值域过滤才是稳定的语言约束。
        if unit == '成' and isinstance(v, (int, float)) and not 0 <= v <= 10:
            continue
        out.setdefault((v if v is not None else m.group(), unit), m.group() + unit)
    return out


# ── secret 的**时间线读法** —— 归一 / 下发闸门 / 判据三处共用的唯一落点 ──────────
#
# ⚠ 这两个函数收的是**未经载体校验的 dict**（归一跑在校验之前），所以它们对形状
# 一律容错、说不出答案就返回「不知道」，而不是兜一个默认值——兜底会把一个本该被
# 载体拒收的形状悄悄补成合法的。

def first_reveal_chapter(secret: dict | None) -> int | None:
    """这条 secret 对读者的首次揭示章；未校验 dict 缺值时如实返回未知。"""
    if not isinstance(secret, dict):
        return None
    value = secret.get('first_reveal_chapter')
    return value if type(value) is int else None


def setup_chapters_of(secret: dict | None) -> list[int]:
    """这条 secret 的埋点章；冷揭示（或形状不认识）一律给 `[]`。

    ⚠ `[]` 在这里是「**没有埋点章可用**」，不是「合法冷揭示」——两者的区别在
    `setup.kind` 上，读它的人要判「有没有隐瞒窗口」就问这个函数，要判「模型
    声明了什么」就直接看 `setup.kind`。合成一个返回值会把这两个问题又粘回一起。
    """
    if not isinstance(secret, dict):
        return []
    setup = secret.get('setup')
    if not isinstance(setup, dict) or setup.get('kind') != 'foreshadowed':
        return []
    return [chapter for chapter in (setup.get('chapters') or [])
            if isinstance(chapter, int)]


# ── relation 的读者可见时点 ────────────────────────────────────────────────

def _chapter(value: Any, default: int | None = None) -> int | None:
    """只接真正的章号；`bool` 虽是 `int` 子类，也不能悄悄变成第 0/1 章。"""
    return value if type(value) is int else default


def relation_visibility_gate(
    relation: dict[str, Any] | None,
    secrets_by_id: dict[str, dict[str, Any]],
) -> int | None:
    """一条 relation 最早可给读者/写手看的章；说不出答案时关闭为 `None`。

    `valid_from_chapter` 是世内成立时点，`visibility` 是读者可见时点，两者正交：

    - `public`：`max(valid_from_chapter, visibility.from_chapter)`；
    - `via_secret`：`max(valid_from_chapter, 所指 secret 的首次揭示章)`。

    显式 visibility 一旦存在就是唯一权威；引用不存在、秘密没有合法 `first`、或
    kind 不认识、或缺整格 visibility 时都不能退回启发式开闸。本仓没有旧 artifact
    迁移/双形状读取；v2 产物由注册版本先关闭式拒掉，这一落点同样只定义 v3 语义。
    """
    if not isinstance(relation, dict):
        return None
    valid_from = _chapter(relation.get('valid_from_chapter'), 1)
    if valid_from is None:
        return None

    visibility = relation.get('visibility')
    if visibility is None:
        return None
    if not isinstance(visibility, dict):
        return None

    kind = visibility.get('kind')
    if kind == 'public':
        visible_from = _chapter(visibility.get('from_chapter'))
        return max(valid_from, visible_from) if visible_from is not None else None
    if kind == 'via_secret':
        secret_id = visibility.get('secret_id')
        secret = secrets_by_id.get(secret_id) if isinstance(secret_id, str) else None
        reveal = first_reveal_chapter(secret)
        return max(valid_from, reveal) if reveal is not None else None
    return None


def relation_visibility_gates(
    entities: dict[str, Any] | None,
    secrets: list[dict[str, Any]] | None,
) -> dict[str, int | None]:
    """全部 relation 的显式可见 gate；Stage4 投影与 typed fact refs 共用此处。"""
    secret_rows = [row for row in (secrets or []) if isinstance(row, dict)]
    secrets_by_id = {
        row['secret_id']: row for row in secret_rows
        if isinstance(row.get('secret_id'), str)
    }
    rels = ((entities or {}).get('relations') or []) if isinstance(entities, dict) else []
    return {
        row['relation_id']: relation_visibility_gate(row, secrets_by_id)
        for row in rels
        if isinstance(row, dict) and isinstance(row.get('relation_id'), str)
    }


def heuristic_relation_gates(
    entities: dict[str, Any],
    secrets: list[dict[str, Any]],
) -> tuple[dict[str, int], list[tuple[Any, Any, int, int]]]:
    """旧关系可见性启发式的完整推导结果，只供 INFO 与显式声明交叉对账。

    返回 `(gates, vanished)`：`gates` 是旧算法对每条 relation 推导出的最早章；
    `vanished` 是被推迟到 `valid_through_chapter` 之后、因而整段消失的关系。
    这份结果不能参与生产投影；实际 gate 只由 `relation_visibility_gates` 给出。

    旧命中口径原样保留：两端 canonical 都出现在 secret.fact，关系在
    `[最早埋点章, 首次揭示章)` 内成立；同一有向人物对若有谓词字面命中，只取字面
    命中的 relation，否则取该对全部候选。冷揭示没有隐瞒窗口，不参与推导。

    ⚠ 只比人名远远不够——主角与主要配角几乎在每条 fact 里都出现。旧算法曾试过
    两种谓词条件，单独用哪种都不成立：要求谓词字面会漏掉「情人」vs「私情」；
    只要共享字符又会把公开的「父女 / 父子 / 保媒对象」推到后章。最后留下的时间
    窗口不依赖措辞，并用同对谓词字面优先缩小多关系误报。它的保守方向一直是宁可
    漏掉一条、退回世内生效章，也不把公开关系错闸。正因为这个判向仍可能错，显式
    visibility 落地后它只能报告分歧，不能再直接改 gate。
    """
    rels = entities.get('relations') or []
    gates = {r.get('relation_id'): (r.get('valid_from_chapter') or 1) for r in rels}
    if not secrets or not rels:
        return gates, []
    chars = (entities.get('characters')
             if isinstance(entities.get('characters'), dict) else {})
    names = {
        cid: strip_distinguisher((value or {}).get('canonical') or '')
        for cid, value in chars.items()
    }
    for secret in secrets:
        fact = secret.get('fact') or ''
        setups = setup_chapters_of(secret)
        reveal = first_reveal_chapter(secret)
        if not setups or reveal is None:
            continue
        lower = min(setups)
        matched = []
        for relation in rels:
            subject = names.get(relation.get('subject_id'))
            obj = names.get(relation.get('object_id'))
            if not (subject and obj) or subject not in fact or obj not in fact:
                continue
            if lower <= (relation.get('valid_from_chapter') or 1) < reveal:
                matched.append(relation)
        by_pair: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
        for relation in matched:
            by_pair.setdefault(
                (relation.get('subject_id'), relation.get('object_id')), []).append(relation)
        for group in by_pair.values():
            literal = [
                relation for relation in group
                if (relation.get('predicate') or '').strip()
                and (relation.get('predicate') or '').strip() in fact
            ]
            for relation in (literal or group):
                relation_id = relation.get('relation_id')
                gates[relation_id] = max(gates.get(relation_id, 1), reveal)
    vanished = [
        (relation.get('relation_id'), relation.get('predicate'),
         gates.get(relation.get('relation_id'), 1), relation.get('valid_through_chapter'))
        for relation in rels
        if relation.get('valid_through_chapter') is not None
        and gates.get(relation.get('relation_id'), 1) > relation.get('valid_through_chapter')
    ]
    return gates, vanished


def secret_keywords(fact: str | None, min_len: int = SECRET_KEYWORD_MIN) -> list[str]:
    """把一条 secret 的 `fact` 切成**可以拿去做字面匹配的关键片段**。

    口径：去掉数字（同一件事换个数字说法照样是泄底，数字反而制造假阴）→
    按全角逗号切 → 留长度 ≥ `min_len` 的片段。

    【判据 11 的位置：**这不是词表**】片段来自 secret 自己的 `fact` 文本，
    不是人手写的词表 ⇒ 它是**结构不变量**，换一本书自动跟着变。

    ⚠⚠ **它只抓【转抄式】泄底，抓不到【改写式】**：判据是纯字面子串包含，
    写手用自己的话把同一件事说出来就绕过去了（原型实测过这个失效）。
    **别把这里报 0 条读成「没泄露」**（判据 7）。

    ⚠ `min_len=6` **没有独立标定**：它是从保真校验那边原地搬过来的，
    当时也没有量过 4/5/6 各自的命中与假阳。要动它先量基线（判据 18/32）。
    """
    return [w for w in (x.strip() for x in CN_NUM_RE.sub('', fact or '').split('，'))
            if len(w) >= min_len]


def secret_norm_index(text: str | None) -> tuple[str, list[int]]:
    """秘密字面探针的 `(normalized, normalized index -> original index)`。

    `secret_keywords` 按产品语义不看数字：「三皇子」换成「五皇子」仍然是
    同一类提前点破。被查文本必须走同一口径；否则 fact 侧的「一直」
    被剥成「直」，成稿侧却保留「一直」，连原样转抄都永远匹配不上。

    回映表仍指向 `text` 的原始下标。判据因此只能引用产物自己写过的
    字节，不会把 `secret.fact` 里的原话抄进报告。

    2026-08-15 全语料纯函数扫描（6 本、39 条 secret）：声明的揭示章原文
    旧口径命中 2/39，当前成稿命中 1/39；两侧统一后这两组真实命中集
    都无变化。这说明剩余盲区主要是改写式泄露，本修复只关闭数字口径的
    系统性假阴，不把字面探针宣传成语义守卫。
    """
    raw = text or ''
    keep = [True] * len(raw)
    for match in CN_NUM_RE.finditer(raw):
        for i in range(match.start(), match.end()):
            keep[i] = False
    original_indexes = [i for i, stays in enumerate(keep) if stays]
    without_numbers = ''.join(raw[i] for i in original_indexes)
    normalized, compact_indexes = norm_index(without_numbers)
    return normalized, [original_indexes[i] for i in compact_indexes]


def locate_secret(needle: str | None, text: str | None) -> tuple[int, int] | None:
    """按 secret 的去数字 + 去标点口径匹配，返回被查文本的原始区间。"""
    key, _ = secret_norm_index(needle)
    if not key:
        return None
    body, indexes = secret_norm_index(text)
    start = body.find(key)
    if start < 0:
        return None
    return indexes[start], indexes[start + len(key) - 1] + 1


def secret_contains(needle: str | None, text: str | None) -> bool:
    """布尔消费方也走 `locate_secret`，避免再写一份匹配口径。"""
    return locate_secret(needle, text) is not None
