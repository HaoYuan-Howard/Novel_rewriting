#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beats.py —— 拍的**确定性切分**。**逐字移植自原型（`Stage4/n4_plan.py`），注释一起搬。**

【这一层的自由度是被【收走】的，不是没给】章内「哪几条源 beat 并成一拍、按什么顺序排」
从前由模型决定，`N4_SYS` 里写着三条硬约束——只并相邻、只并同层、按写作顺序排——
而代码里**一个门都没有**。实测盘上三条**全被违反过**（重排 · 跳过中间那条并两头 ·
hearsay+present 并成一拍）。
⇒ 收走之后它们成了 `group_beats` 的**后置条件**：`beat_seq` / `beat_adjacent` /
`beat_layer` 三条 HARD **红了只有两种可能——产物是旧的，或本模块坏了**。

⚠ **收走的是一个「无守卫、高方差的自由变量」，不是一个已观测到的质量故障**——
别把它记成后者：另一次实测已经把「计划层融合 beats」当成风格失败的嫌疑排除过
（161/161 events 与 stage2 逐字一致、0 丢拍）。**没有守卫 ≠ 已经出事。**

⚠ **为什么不是纯 1:1**：源拍中位只有 113~122 字、27~29% 不足 80 字（最小 13 字），
而写手每拍有约 56 字的自然下限 —— 纯 1:1 会把某本 2/14 章顶破篇幅上界，最差 1.33×。
只吸收碎拍则「<50 字的拍」归零、篇幅上浮最差 1.03×。

【本模块吃 `dict` 不吃模型】理由见 `novel/stages/stage4/__init__.py` ③。
"""
from __future__ import annotations

import re
from typing import Any

from contracts.invariants import BEAT_MIN_CHARS, HINGE_GROUPS
from novel.core.spans import locate_spans

#: 一条 hinge 拆成「点」时的段落边界。⚠ **按段落拆，不按句子拆**：见 `verbatim_pieces`。
_PARA_SPLIT = re.compile(r'\n\s*\n')


def beat_index(blueprints: dict[str, Any],
               main_text: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`beat_id` → 写作侧要的原文材料，**按引用取值**。

    · `source_chars` 由 `beat.span` **确定性算出**（不是模型填的）；
      算不出给 `None`，**绝不退回估算**——估算的方向会整个反过来。
    · `hinges` 是 stage2 落盘时**已回取成原文字节**的片段，
      所以这里搬运的一定是原文，不是模型打出来的串。
    · 另外三样是「写手落笔时必须知道、而 `event` 文字本身不承载」的：
      一段转述若不标出来会被写成当场发生的戏；地点与在场者同理，闭卷时无从推断。
    """
    text = {m.get('chapter_ordinal'): (m.get('text') or '') for m in main_text}
    bps = blueprints.get('blueprints') or []
    spans, _problems, _loose = locate_spans(bps, text)
    size = {bid: e - s for chs in spans.values() for bid, s, e in chs}
    idx: dict[str, dict[str, Any]] = {}
    for b in bps:
        o = b.get('chapter_ordinal')
        for be in (b.get('beats') or []):
            idx[be.get('beat_id')] = {
                'ord': o,
                # 【写手要写的内容本身也走引用】不再由规划器转述一遍，见 `context._resolve_beats`
                'event': be.get('event'),
                'source_chars': size.get(be.get('beat_id')),
                'hinges': [{'kind': h.get('kind'), 'text': h.get('text')}
                           for h in (be.get('hinges') or []) if (h.get('text') or '').strip()],
                'time_layer': be.get('time_layer'),
                'place': be.get('place'),
                'present_char_ids': be.get('present_char_ids') or [],
            }
    return idx


def group_chars(group: list[str], bidx: dict[str, dict[str, Any]]) -> int | None:
    """一拍的源字数 = 组内各源 beat 之和；**任一条未知就整组未知**（不拿已知的那几条充数）。

    ⚠⚠ **唯一一份口径**（判据 14）：`group_beats` 判「够不够 `BEAT_MIN_CHARS`」、
    恒等基线给预算、量纲表报给规划器的数，**全走这一个函数**。
    三处各写一遍的话，喂给模型的基准就会与代码判它时用的基准分家，
    **而错的往往是那个跨口径的运算**。
    """
    ns = [(bidx.get(x) or {}).get('source_chars') for x in group]
    return None if (not ns or any(n is None for n in ns)) else sum(ns)


def plan_beat_id(chapter_ordinal: Any, seq: int) -> str:
    """规划侧的拍编号。**`p` 前缀与 stage2 的 `b` 分开**——

    同名不同义是最好的漂移温床，而这两个东西恰恰长得很像：
    一个是**源 beat**，一个是**由若干源 beat 组成的拍**。
    """
    return f'p{int(chapter_ordinal):02d}_{int(seq):02d}'


def group_beats(blueprints: dict[str, Any], main_text: list[dict[str, Any]],
                bidx: dict[str, dict[str, Any]] | None = None) -> dict[Any, list[list[str]]]:
    """把每章的源 beat 确定性地分成【拍】→ `{chapter_ordinal: [[beat_id, …], …]}`。

    规则只有一条：**只吸收碎拍**。低于 `BEAT_MIN_CHARS` 的组并进它**同叙述层的、
    较小的那一侧**邻居（同大取左，保证确定），重复到没有可并的为止。
    **够大的拍一条不动**——这样保住了原文自己的轻重，不会把一章削成一排等长的拍。

    三条性质是**结构性的，不是靠 prompt 叮嘱来的**：
      · 顺序恒等于源序——只在相邻位置合并，从不换位；
      · 只并相邻——组永远是源序上的一段**连续区间**；
      · 只并同层——`time_layer` 不同的两组**永不相邻合并**。

    ⚠⚠ **`source_chars` 未知的拍独立成组，绝不被吸收**：span 锚不到时它的字数是
    **「不知道」，不是 0**。把它并进邻居会让组预算看起来正常，于是
    「stage2 那一拍锚点坏了」这个真信号**被一次合并悄悄掩盖掉**。
    留它独立，`budget_int` 会照常红出来。
    ⚠ 它**也不参与「哪一侧更小」的比较**——未知不能拿去比大小。
    """
    if bidx is None:
        bidx = beat_index(blueprints, main_text)

    def size(g: list[str]) -> int | None:
        return group_chars(g, bidx)

    out: dict[Any, list[list[str]]] = {}
    for b in (blueprints.get('blueprints') or []):
        beats = b.get('beats') or []
        layer = {be.get('beat_id'): be.get('time_layer') for be in beats}
        groups: list[list[str]] = [[be.get('beat_id')] for be in beats]   # 先各自成组
        while len(groups) > 1:
            for i, g in enumerate(groups):
                n = size(g)
                if n is None or n >= BEAT_MIN_CHARS:
                    continue
                # 组内叙述层恒定（只并同层是不变量），取任一成员即可代表整组
                lo = groups[i - 1] if i > 0 and layer.get(groups[i - 1][0]) == layer.get(g[0]) else None
                hi = (groups[i + 1] if i + 1 < len(groups)
                      and layer.get(groups[i + 1][0]) == layer.get(g[0]) else None)
                if lo is not None and size(lo) is None:
                    lo = None
                if hi is not None and size(hi) is None:
                    hi = None
                if lo is None and hi is None:
                    continue                                   # 没有同层邻居：保持独立
                if hi is None or (lo is not None and size(lo) <= size(hi)):   # type: ignore[operator]
                    groups[i - 1] = lo + g                     # type: ignore[operator]
                else:
                    groups[i + 1] = g + hi
                groups.pop(i)
                break                                          # 每轮只合一次，重新从头扫
            else:
                break                                          # 一轮下来没并动：收敛
        out[b.get('chapter_ordinal')] = groups
    return out


def group_map(groups: dict[Any, list[list[str]]]) -> dict[str, list[str]]:
    """`{chapter_ordinal: [[bid,…],…]}` → `{plan_beat_id: [bid,…]}`（全书唯一键）。"""
    return {plan_beat_id(o, i): g
            for o, gs in groups.items() for i, g in enumerate(gs, 1)}


def verbatim_pieces(hinges: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[tuple[Any, str]]]:
    """把一条 beat 的 hinges 拆成【可以单独放进去的点】，按四组归类 → `({group: [piece,…]}, unknown)`。

    ⚠⚠ **唯一一份实现**：`context._resolve_beats`（真正下发给写手的字节）与
    `measure.source_chars_table`（告诉规划器这一拍有多少字压不掉）**都调它**。
    两边各写一份拆分逻辑，报给规划者的数就会与写手实际收到的字节对不上
    ——一个量纲只能有一个口径，**而错的往往不是某个数，是那个跨口径的运算**。

    【四组守的是**不同的维度**，不是「守多少」的四档】全塞进一个叫 `verbatim` 的数组，
    写手会把「因果前提」也当成必须逐字出现的句子，硬塞背景交代——而它要的只是命题成立。
    ⚠ 映射表就是 `contracts.invariants.HINGE_GROUPS`，**这里不抄第二份**；
    「它的 key 必须与 `HINGE_KINDS` 逐字一致」由 `tests/test_invariants.py` 钉着
    （原型是模块加载时 `assert`，改成测试更好：它不会在 import 期炸掉整条命令行）。

    【跨段落的 hinge 必须按段落边界拆成独立的点】stage2 的「要害台词」允许摘「一两句」，
    而本语料是「一句一段」，于是一条 hinge 常常跨过 `\\n\\n` 把两三句捆在一起
    （实测某本 60 条 verbatim 里 13 条跨段落、另一本 81 条里 11 条）。
    那样的一条**就是一个「段」而不是一个「点」**——写手要么连原文的段落结构一起复现，
    要么整组放弃。**实测某章 4 条 verbatim 全部跨段落，成稿把它们一条不剩地丢光。**
    在这里按段落边界拆开：语义一点没变（都还是原文字节），但写手能一条一条地满足。

    【未知 kind 不下发，也不静默】曾经这里是 `HINGE_GROUPS.get(kind, 'verbatim')`——
    模型自造的 kind（实测「行动描述」）于是被**升级**成「原样写入，一个字都不能改」，
    而那是一句叙述文，正是「把段当点发」那条禁令挡的东西。
    ⇒ 现在丢弃并把它交给调用方报出来。
    ⚠ **不在这里入终态**：一条旧数据不该把 `stage4.context` 这条 0 成本重算路永久堵死。
    """
    out: dict[str, list[str]] = {}
    unknown: list[tuple[Any, str]] = []
    for h in hinges:
        kind = h.get('kind')
        g = HINGE_GROUPS.get(kind)
        if g is None:
            unknown.append((kind, (h.get('text') or '')[:20]))
            continue
        for piece in _PARA_SPLIT.split(h.get('text') or ''):
            piece = piece.strip()
            if piece:
                out.setdefault(g, []).append(piece)
    return out, unknown
