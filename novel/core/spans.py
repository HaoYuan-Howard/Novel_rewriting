#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spans.py —— 正式 `SourceSpan` 的**校验与读取**，以及内容字符空间那把尺子。

【它为什么在 `novel/core/` 而不是某一层里】产地是 stage2，而**第一个消费方是 stage4**：
逐拍 `source_chars` 由这里的区间长度算出来，`group_beats` 判「够不够 `BEAT_MIN_CHARS`」、
喂给规划器的量纲表、写手实际收到的字节，**全走这一份**（判据 25）。

⚠⚠ **本模块的 `content_only` 与 `novel/core/text.py::norm` 长得几乎一样，而它们【不是】
同一个东西——合并会静默改动定位结果。** 两者的取值面：

  · 本模块 `\\w`            —— Unicode 词字符：另含全角字母数字（`Ａ` `０`）、假名、
                              下划线、带音标的拉丁字母、CJK 扩展区（`𠮷`）
  · `text.norm` `[^一-鿿A-Za-z0-9]` —— 只留 CJK 基本区 + ASCII 字母数字

盘上三本实测**逐字节相同**（31/31 章正文 · 502/502 个锚点），但那是**这三本的性质，
不是这两个正则的性质**：`第Ａ章０１` 在本模块是 5 个内容字、在 `norm` 里是 2 个。
而 `content_index` 的 `cmap` 是**下标映射**——少两个内容字，后面每一条锚点的原文偏移全错，
**而它不会报错**，只会让某几拍的 `source_chars` 变成另一个数。
⇒ 两份各留各的，配一条会红的测试（`tests/test_core.py`）钉住这个分歧。

【这里曾经还有一整套「在原文里搜模型逐字抄回来的锚点」的装置，2026-08-19 整批删除】
`walk_spans`（首尾 locator 的游标行走）/ `_find_or_recover`（锚点被改掉一两个字时按最长
公共子串救一次）/ `_find_exact_raw` / `extend_punct` / `HINGE_LCS_MIN` 全部随
`bounded-fidelity-v3` 下线：`stage2.blueprint` 的模型**不再转写原文**，它交段号，
区间由 `novel/stages/stage2/paragraphs.py` 的段落表查出来 ⇒ 那套装置**零生产消费方**。
⚠ 留着它不是「以防万一」，是留一条有测试、看起来还活着的死路
（`CLAUDE.md`：被本次改动作废的路径要跟着删）。
⚠ **`lcs` 不在删除之列**：stage5 的 `must_appear_missing` 与 stage6 都在用它，
它比那套装置早进来，也不属于它。
"""
from __future__ import annotations

import re
from typing import Any

from contracts.invariants import SPAN_RECOVERY

#: 汉字/字母/数字；标点与空白都不算内容。⚠ 见模块 docstring：**别拿它去换 `text.norm`**。
CONTENT = re.compile(r'\w')


def content_index(text: str) -> tuple[str, list[int]]:
    """`(content, cmap)`：`content` = 只留内容字符的串；`cmap[i]` = 第 i 个内容字在原文里的下标。

    【为什么要在「内容字符空间」里定位锚点，而不是逐字比对原文】
    实测 262 个 span 锚点里有 **28 个逐字比不上**，根因两个，**都与内容无关**：
      ① 原文是「每句一段」排版（某本 `avg_paragraph_chars=14.7`），句间是 `\\n\\n`，
         模型照抄时把换行压掉了；
      ② 原文用半角 `?` `!` `,`，模型统一改成了全角。
    归一化之后 **262/262 全部命中**。这与「要求 LLM 逐字复现标点与空白不可靠、
    而它复现的**内容**是准的」是同一个结论——**把不可靠的那一维归一化掉，
    别去 prompt 里加禁令**（实测禁令压不住）。
    """
    chars: list[str] = []
    cmap: list[int] = []
    for i, c in enumerate(text):
        if CONTENT.match(c):
            chars.append(c)
            cmap.append(i)
    return ''.join(chars), cmap


def content_only(s: str | None) -> str:
    """只留内容字符。**需要同口径比对的地方一律走它，别各写一份。**"""
    return ''.join(CONTENT.findall(s or ''))


def lcs(a: str, b: str) -> tuple[int, int]:
    """最长公共**子串** → `(在 a 里的起点, 长度)`。比的都是锚点/材料级短串，`O(nm)` 足够。

    ⚠ **子串不是子序列**：要的是「这段话原样出现过多长的一截」，
    子序列会让「羽林卫特制的箭矢带有逆刺」与一段毫不相干的文字算出很高的覆盖率。

    【第一个消费方是 stage5 的 `must_appear_missing`】那一组材料的契约是
    **「必须在场并起作用，怎么描写随你」** ⇒ **不能按整串比对**（那是 `verbatim` 的查法）。
    实测吃过一次：材料是「羽林卫特制的箭矢带有逆刺」，成稿写「羽林卫的箭，箭簇带逆刺」，
    在场与作用都保住了（后文还有「倒刺便在伤口里绞动」），却被整串匹配判成缺失——
    **判据与它自己声明的维度不一致**。改看最长公共子串之后：换说法能过，整个没写才报。
    """
    best_i = best_n = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_n:
                    best_n, best_i = cur[j], i - cur[j]
        prev = cur
    return best_i, best_n


_TERMINAL_SUFFIX = frozenset('\u3002\uff01\uff1f?!\u2026.')


def _overlapping_count(text: str, needle: str) -> int:
    """统计所有可能起点；``str.count`` 会漏掉重叠命中。"""
    if not needle:
        return 0
    count, start = 0, 0
    while (hit := text.find(needle, start)) >= 0:
        count += 1
        start = hit + 1
    return count


def extend_terminal_punct(text: str, end: int) -> int:
    """只接住句末闭合标点，不把冒号、开引号等下一段语义吞进材料。"""
    while end < len(text) and text[end] in _TERMINAL_SUFFIX:
        end += 1
    return end


def locate_unique_span(text: str, locator: str) \
        -> tuple[int, int, str] | int | None:
    """在一个已限定的原文面内唯一定位短 locator。

    返回 ``(start, end, recovery)`` 表示唯一命中；返回 ``int``（≥2）表示
    多处命中；返回 ``None`` 表示找不到。原样字节优先，因为标点可能正是重复
    字面的唯一消歧信息；原样找不到才退到内容字符空间。这里故意没有 LCS 兜底：
    locator 的职责是把事实绑定到一处精确原文，近似命中会把证据静默绑错。

    这是 Stage2 所有“模型给短 locator、代码回取正式 SourceSpan”的唯一性尺子。
    调用方决定 ``text`` 是一个 beat、一个段还是指定章节，但不得另写 ``find()``
    并取第一处。
    """
    raw = locator.strip()
    if raw:
        count = _overlapping_count(text, raw)
        if count == 1:
            hit = text.find(raw)
            return hit, extend_terminal_punct(text, hit + len(raw)), 'exact'
        if count > 1:
            return count
    content, cmap = content_index(text)
    needle = content_only(locator)
    if not needle or not cmap:
        return None
    count = _overlapping_count(content, needle)
    if count == 0:
        return None
    if count > 1:
        return count
    at = content.find(needle)
    return cmap[at], extend_terminal_punct(
        text, cmap[at + len(needle) - 1] + 1), 'normalized'


def exact_span(source: Any, text: str, after: int = 0) -> tuple[int, int, str | None]:
    """验证正式 source span 的偏移、原文字节、规范化值和恢复方式。"""
    if not isinstance(source, dict):
        return 0, 0, 'missing exact source span'
    start, end = source.get('start'), source.get('end')
    if type(start) is not int or type(end) is not int:
        return 0, 0, 'source span offsets are not integers'
    if start < after or end <= start or end > len(text):
        return 0, 0, f'invalid source span [{start}:{end}] after {after}'
    raw = text[start:end]
    if source.get('text') != raw:
        return 0, 0, 'source span text does not match offsets'
    normalized = content_only(raw)
    if not normalized:
        return 0, 0, 'source span has no content characters'
    if source.get('normalized') != normalized:
        return 0, 0, 'source span normalized text does not match'
    # ⚠ 取值面从**契约**取，不在这里手抄一份：`SourceSpan.recovery` 的 `Literal`
    #   收窄过一次（`lcs` 随最长公共子串救回装置一并下线），手抄的那份不会跟着改。
    if source.get('recovery') not in SPAN_RECOVERY:
        return 0, 0, 'source span recovery is invalid'
    return start, end, None


def locate_spans(bps: list[dict[str, Any]],
                 text_of: dict[Any, str]) -> tuple[
                     dict[Any, list[tuple[str, int, int]]], list[tuple[Any, str]]
                 ]:
    """严格读取正式 ``SourceSpan``，返回每条 beat 的原文字符区间。

    返回 ``(spans, problems)``：
      · `spans` = `{chapter_ordinal: [(beat_id, start, end), …]}`
      · `problems` = `(chapter_ordinal, detail)`，逐条点名无效精确区间
    """
    spans: dict[Any, list[tuple[str, int, int]]] = {}
    problems: list[tuple[Any, str]] = []
    for blueprint in bps:
        ordinal = blueprint.get('chapter_ordinal')
        text = text_of.get(ordinal, '')
        cursor = 0
        for beat in (blueprint.get('beats') or []):
            start, end, problem = exact_span(beat.get('span'), text, cursor)
            if problem:
                problems.append((ordinal, f'{beat.get("beat_id")}: {problem}'))
                continue
            spans.setdefault(ordinal, []).append((beat.get('beat_id'), start, end))
            cursor = end
    return spans, problems
