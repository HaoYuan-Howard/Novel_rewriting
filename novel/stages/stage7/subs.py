#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subs.py —— 替换器与残留探针。**本层专用，刻意不进 `novel/core/`。**

【为什么不复用 stage4 那个 `_rename_all`，这是一条论证不是偏好】
那一个的键是**在原文里出现 0 次**的自造标签，任何位置替换都不可能破坏真词，
所以它敢用链式 `str.replace`。**真名替换的键是原文里真有的词，这个论证整个不成立**
（`A→B` 与 `B→C` 一起跑，A 会变成 C）。⇒ 另起一份，单次扫描，游标不回头。

【为什么不放 `core`】`docs/schema/stage7.md` §七.7：只有一个节点用它，共享层保持薄。
真正跨层共用的那几个（姓氏族群、亲属称谓判定）已经进了 `novel/core/naming.py`。

⚠⚠ **本模块有三处「数一个字面出现了几次」，而三处的正确口径各不相同。**
弄错任何一处都不会报错，只会让某个数字静默地变成另一个意思：

| 函数 | 口径 | 为什么 |
|---|---|---|
| `surface_hits` | **单次扫描的独立命中**，绝不 `str.count` | 这个数**下发给模型**，虚高会让它按错的轻重分配注意力 |
| `apply_subs` 的 `claimed` | 认领区间，**含恒等条目认领的那一半** | 残留判据靠它才有正确口径 |
| `residual_scan` 的 `total` | **故意** `str.count`，**要含嵌套** | 它量的是单字姓字的总出现次数，那本来就该含嵌套 |
"""
from __future__ import annotations

import re
from collections import Counter

from contracts.invariants import (
    DERIVED_ALIAS_SUFFIXES, RESIDUAL_CONTEXT_CHARS, RESIDUAL_HIGH_STEMS,
    RESIDUAL_HIT_CHARS,
)
from novel.core.naming import strip_distinguisher


def sub_table(subs: dict[str, str],
              kept: tuple[str, ...] | list[str] | set[str] = ()
              ) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    """→ `(pattern, table)`。`table` = `{字面: 换成什么}`，**保留项映射到它自己**。

    两件事，都是承重的：

    ① **最长优先靠排序。** Python 的 `re` alternation 是 leftmost-first
       （谁写在前面谁先试），**不是最长匹配**。实测某本「阿禾」在原文出现 5 次，
       其中 3 次是「阿禾姊姊」的一部分；「孟青禾」⊃「青禾」同理。
       短的排前面会切出半截残串。

    ② **保留项必须进 alternation，作恒等条目。** 它们不改一个字节，唯一的作用是
       **抢在更短的映射条目之前把那段字节认领走**。这是一道**结构性**的防线
       （`R-10.3`）：实测某本 `char_011` 的唯一称呼是单字「姐」，而「姐姐」
       （另两个角色）成稿里有 4 处。「姐」若进了替换面，单次扫描会把 4 处「姐姐」
       全切成「新X姐」——而多主那条判据抓不到它（「姐」是单主），
       只剩一道**词表**防线。把「姐姐」放进正则，这件事就与词表判得准不准无关了。
       ⚠ 它挡不住 registry 没收的词（「大姐」「姐妹」），所以最短长度那道仍然必需。
    """
    table = {k: k for k in kept}
    table.update(subs)          # 冲突时换赢（`build_subs` 已保证两者不交）
    if not table:
        return None, table
    pat = re.compile('|'.join(re.escape(k)
                              for k in sorted(table, key=len, reverse=True)))
    return pat, table


def apply_subs(text: str | None, subs: dict[str, str],
               pat: re.Pattern[str] | None = None,
               kept: tuple[str, ...] | list[str] | set[str] = ()
               ) -> tuple[str, Counter, list[tuple[int, int]]]:
    """→ `(新文本, Counter{真被换掉的键: 次数}, claimed)`。**单次扫描。**

    `claimed` = 输出里被 alternation **认领过**的全部区间——**替换写出来的和
    恒等留下的都算**。残留判据靠它才有正确口径：字面 `.count(old) == 0` 会在一个
    **合法**情形上误报——某个被映射的称呼是**保留称呼的子串**时（「阿禾」被换、
    「阿禾姊姊」被留），保留下来的那段里必然还有它，而那不是残留、
    是那条保留决定的直接后果。
    ⚠ **只记「被替换的」那一半也不够**（原型最初就是那么写的，被测试当场抓住）：
    恒等条目认领的区间同样要记，否则保留串里的子串照样会被算成残留。

    【为什么必须单次扫描】链式 `str.replace` 会级联（`A→B` 与 `B→C` 一起跑，A 变 C）。
    单次扫描游标不回头，**级联在结构上不可表示**（`R-10.2`）。
    """
    if not text:
        return '', Counter(), []
    if pat is None:
        pat, table = sub_table(subs, kept)
    else:
        table = {k: k for k in kept}
        table.update(subs)
    if pat is None:
        return text, Counter(), []
    counts: Counter = Counter()
    claimed: list[tuple[int, int]] = []
    out: list[str] = []
    last = grown = 0
    for m in pat.finditer(text):
        k = m.group()
        new = table[k]
        out.append(text[last:m.start()])
        base = m.start() + grown        # 该匹配在**输出**坐标系里的起点
        claimed.append((base, base + len(new)))
        if new != k:                    # 恒等条目只是屏蔽，不算一次替换
            counts[k] += 1
            grown += len(new) - len(k)
        out.append(new)
        last = m.end()
    out.append(text[last:])
    return ''.join(out), counts, claimed


def unclaimed_residual_keys(text: str,
                            keys: dict[str, str] | set[str] | frozenset[str],
                            claimed: list[tuple[int, int]]) -> set[str]:
    """返回落在替换器认领区间之外的键。

    不能直接写 ``key in text``：替换结果本身可能合法地包含旧键字面。例如门牌号
    ``001`` 会出现在恒等基线生成的 ``⟦char_001_00⟧`` 占位符里；那段区间是
    替换器刚刚写出的，不是未替换残留。认领坐标是 `apply_subs` 的既有契约，
    正式改名与恒等预检必须共用这一把尺子。
    """
    residual: set[str] = set()
    for key in keys:
        for match in re.finditer(re.escape(key), text):
            if not any(start <= match.start() and match.end() <= end
                       for start, end in claimed):
                residual.add(key)
                break
    return residual


def surface_hits(universe: set[str] | frozenset[str],
                 targets: list[str] | tuple[str, ...]) -> Counter:
    """每个称呼在**替换目标**里的独立命中数（单次扫描，最长优先）。

    ⚠⚠ **不能用 `str.count`**：称呼之间大量嵌套，`count` 会把长称呼的字节
    **重复计给短称呼**。实测某本「阿禾」`count`=4 而独立命中只有 **1**
    （另外 3 次在「阿禾姊姊」里）；「老王」`count`=1 而独立命中 **0**
    （那 1 次在「富商老王」里）。
    **这个数是下发给模型的材料**——下发一个虚高三倍的数字，
    模型就会按错的轻重去分配注意力，把一次性绰号当主名去精心起名。

    实现上借 `sub_table({}, universe)`：把**全部**下发称呼当恒等条目编成一条
    alternation、按长度倒排 ⇒ 最长优先；然后单次 `finditer`，谁认领了那段字节算谁的。
    """
    pat, _table = sub_table({}, universe)
    hits: Counter = Counter()
    if pat is None:
        return hits
    for t in targets:
        for m in pat.finditer(t or ''):
            hits[m.group()] += 1
    return hits


def mask_known(text: str | None, surfaces: set[str] | frozenset[str]) -> str:
    """把所有已知称呼掩成**等长**占位 → 残留探针的输入。

    **这一步是探针有判别力的全部原因。** 不掩码直接扫姓字，噪声压倒信号
    （实测某本「谈」68 次、「祈」22 次）；掩码之后剩余量小到人扫得完
    （96→4 · 162→17）。**等长**替换是为了让报出来的位置还对得上原文。
    """
    if not surfaces:
        return text or ''
    pat = re.compile('|'.join(re.escape(s)
                              for s in sorted(surfaces, key=len, reverse=True)))
    return pat.sub(lambda m: '　' * len(m.group()), text or '')


def derived_alias_scan(
    post_units: list[tuple[str, str]],
    characters: list[dict],
    kept: set[str] | frozenset[str] | tuple[str, ...] | list[str] = (),
) -> list[dict]:
    """扫描「旧名字尾字 + 关系后缀」这类人物表可推导的漏网 surface。

    例：`谢箬→苏瑶` 能推出候选 `箬姐姐→瑶姐姐`。它不含旧姓，
    `residual_scan` 按构造看不到；又未必在 registry 里，完整 surface 替换也够不着。

    ⚠ 只报候选，不自动替换。若同一个旧尾字从人物表推得多个新尾字，`suggest=None`，
    如实交给人裁决；显式 kept 的长称呼先等长掩码，避免把“决定保留”重报成漏网。
    """
    suggestions: dict[str, set[str]] = {}
    for character in characters:
        if not character.get('renamed'):
            continue
        pairs = [(character.get('old_canonical'), character.get('new_canonical'))]
        pairs.extend((row.get('old'), row.get('new'))
                     for row in (character.get('aliases') or []))
        for old, new in pairs:
            old = strip_distinguisher((old or '').strip())
            new = strip_distinguisher((new or '').strip())
            if len(old) < 2 or len(new) < 2 or old[-1] == new[-1]:
                continue
            for suffix in DERIVED_ALIAS_SUFFIXES:
                suggestions.setdefault(old[-1] + suffix, set()).add(new[-1] + suffix)

    kept_set = {surface for surface in kept if surface}
    out: list[dict] = []
    for where, text in post_units:
        searchable = mask_known(text, kept_set)
        for old in sorted(suggestions, key=lambda value: (-len(value), value)):
            values = suggestions[old]
            suggestion = next(iter(values)) if len(values) == 1 else None
            for match in re.finditer(re.escape(old), searchable):
                start = match.start()
                out.append({
                    'where': where,
                    'unit': old,
                    'suggest': suggestion,
                    'context': (text or '')[
                        max(0, start - RESIDUAL_CONTEXT_CHARS):
                        start + len(old) + RESIDUAL_CONTEXT_CHARS
                    ].replace('\n', ' '),
                })
    return out


def residual_scan(pre_units: list[tuple[str, str]], post_units: list[tuple[str, str]],
                   stems: frozenset[str] | set[str], surfaces: set[str] | frozenset[str],
                  surname_map: dict[str, str] | None = None,
                  canonical_map: dict[str, str] | None = None,
                   ) -> tuple[list[dict], dict[str, dict]]:
    """漏网叫法的候选清单 + **覆盖率基线**。`*_units` = `[(where, text)]`。

    → `(residual[], baseline{stem: {total, covered, residual, ratio}})`
      · `total`    = 该字在**替换前**全文里的出现次数（分母；量在 pre）
      · `residual` = 该字在**替换后**全文、且掩掉全部已知称呼之后仍剩下的次数
      · `ratio`    = covered/total

    **分母必须量在 pre**：比率的分母要是被测过程改不动的量。量在 post 上，
    改了名之后旧姓字本来就所剩无几，覆盖率会恒等于 0，这个比率就不携带任何信息。

    ⚠ **这里的 `total` 是三处 count 里【故意含嵌套】的那一处**（见模块头那张表）：
    它量的是单字姓字的总出现次数，嵌套本来就该算进去。**别连坐改成独立命中。**

    **覆盖率就是判别器，实测分得很干净**：≥90% 的字，残留几乎必然是真漏网；
    <50% 的字，残留基本是普通词（「许」33% → 许久/不允许，全假）。

    ⚠⚠ **但这条判别器有一个会反向的死角：当漏掉的那个形就是该姓的主用法时，
    覆盖率低是【由构造决定的】，而不是「这些残留是普通词」的证据。**
    实测 `沈` 5 次里 3 次是漏网的「沈家」⇒ 40%；`孙` 10 次里 4 次是「孙家」⇒ 60%。
    两个都落在「基本是普通词」那一侧，而**两个都是真漏网**。
    ⇒ **两个信号不一致时以 `confidence` 为准**：它是结构信号（`<姓>家/府/氏/宅/门`），
    不受这个反馈回路影响，实测 6/6 全真。排序已经把 `high` 排在最前。

    **基线是判据 31 强制的**：没有分母，「residual 只有 4 条」读不出是探针管用
    还是探针瞎了。所以**残留为 0 时也要输出这四个数**。

    ⚠ **它只报候选，不改任何东西。** 剩余里必然混着固有假阳（「许久」「谈话」），
    靠调阈值消不掉（中文没有词边界）——处置是人勾选 → `extra_subs` → `--apply-edit`
    重放。它也**永远是 info**：那几条假阳不许拦住交付。
    """
    residual = {st: 0 for st in stems}
    total = {st: 0 for st in stems}
    out: list[dict] = []
    for _where, text in pre_units:
        for st in stems:
            total[st] += (text or '').count(st)
    for where, text in post_units:
        masked = mask_known(text, surfaces)
        split_at: dict[int, tuple[str, str | None]] = {}
        for old, new in sorted((canonical_map or {}).items(), key=lambda item: -len(item[0])):
            if len(old) < 2 or old == new:
                continue
            # A canonical may be broken at any one or more of its gaps.  The
            # old implementation required *every* gap to contain punctuation,
            # so ``温夏、白`` escaped even though ``汤、临`` was detected.
            # Permit empty gaps in the regex, then require 1..3 separator
            # characters across the complete match below.
            separator = r'([、，。！？…·\s]{0,3})'
            pattern = re.compile(
                re.escape(old[0])
                + ''.join(separator + re.escape(ch) for ch in old[1:]))
            for split_match in pattern.finditer(text or ''):
                separators = list(split_match.groups())
                separator_chars = sum(len(value) for value in separators)
                if not 1 <= separator_chars <= 3:
                    continue
                # A literal known surface is masked before this point; a split spelling is not.
                # Still require the old stem itself to remain visible so two detectors cannot
                # claim the same already-recognised span.
                if masked[split_match.start():split_match.start() + 1] != old[:1]:
                    continue
                suggestion: str | None = None
                if len(old) == len(new):
                    suggestion = ''.join(
                        ch + (separators[index] if index < len(separators) else '')
                        for index, ch in enumerate(new))
                split_at.setdefault(split_match.start(), (split_match.group(), suggestion))
        for st in stems:
            for m in re.finditer(re.escape(st), masked):
                residual[st] += 1
                i = m.start()
                hit = (text or '')[i:i + RESIDUAL_HIT_CHARS]
                new_sur = (surname_map or {}).get(st)
                split = split_at.get(i)
                suffix = next(
                    (candidate for candidate in sorted(
                        RESIDUAL_HIGH_STEMS, key=lambda value: (-len(value), value))
                     if (text or '').startswith(candidate, i + len(st))),
                    '',
                )
                if split is not None:
                    unit, suggestion = split
                    confidence = 'high'
                elif suffix:
                    unit = st + suffix
                    suggestion = new_sur + suffix if new_sur else None
                    confidence = 'high'
                else:
                    nxt = (text or '')[i + len(st):i + len(st) + 1]
                    unit = st + nxt if nxt else None
                    suggestion = new_sur + nxt if (new_sur and nxt) else None
                    confidence = 'low'
                out.append({
                    'stem': st,
                    'where': where,
                    'hit': hit,
                    'context': (text or '')[max(0, i - RESIDUAL_CONTEXT_CHARS):
                                            i + RESIDUAL_CONTEXT_CHARS].replace('\n', ' '),
                    # `<姓>家/府/氏/宅/门` 实测在两本上 6/6 全真。排前面标 high，
                    # **但仍不自动换**——「不允许家人」里就含「许家」，
                    # 自动替换会写出「不允苏家人」。
                    'confidence': confidence,
                    # 建议替换对：`unit` = 旧姓 + 紧邻的一个字（`沈家`），
                    # `suggest` = 本书新姓 + 同一个字（`林家`）。**只是给人省一次敲字**，
                    # 不参与任何判定，也绝不自动生效——人勾中之后它走 `extra_subs`，
                    # 于是 `build_subs` 那一整套检查一条都不会被绕过。
                    # 没有新姓可查时留 `None`，**不猜**。
                    'unit': unit,
                    'suggest': suggestion,
                })
    baseline: dict[str, dict] = {}
    for st in stems:
        cov = max(0, total[st] - residual[st])
        baseline[st] = {
            'total': total[st], 'covered': cov, 'residual': residual[st],
            # ⚠ `total == 0` ⇒ `None`，**不是 `0.0`**：那种 stem 只可能来自
            #   `surname_map` 的一个原文里压根没有的键。压成 0.0 会被读成
            #   「覆盖率 0%」，在这张按覆盖率排序的表上是最坏的读法。
            'ratio': round(cov / total[st], 2) if total[st] else None,
        }
    out.sort(key=lambda r: (r['confidence'] != 'high',
                            -(baseline[r['stem']]['ratio'] or 0),
                            r['stem'], r['where']))
    return out, baseline
