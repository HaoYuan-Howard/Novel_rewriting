#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spans.py —— 把 `beat.span` 的首尾锚点在原文里**确定性定位**成字符区间。
**逐字移植自原型（`Stage2/stage2_common.py`），注释一起搬。**

【它为什么在 `novel/core/` 而不是某一层里】产地是 stage2（锚点是那一层的模型写的），
而**第一个消费方是 stage4**：逐拍 `source_chars` 由这里的区间长度算出来，
`group_beats` 判「够不够 `BEAT_MIN_CHARS`」、喂给规划器的量纲表、写手实际收到的字节，
**全走这一份**。F5 移植 stage2 时它已经在了，不必重写第二份（判据 25）。

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

【`recover` 那条臂是 F5-2 进来的，而它默认关着】原型 `_find_or_recover` 的那条
「锚点被模型改掉一两个字时按最长公共子串救一次」的分支，**只在 stage2 落盘时
（`normalize_verbatim`）开着，校验器一律不开**——理由原文照抄：
「否则『能定位』会把『锚点不是原文字节』这件事盖掉」。
⇒ 它是 `walk_spans` 的一个 **keyword-only 参数且默认 `False`**，
而 `locate_spans` / `has_spans`（量纲侧与校验侧）**根本不暴露它**：
那两个函数的默认值就是终态，不是「还没接上」。
⚠ 而 `lcs` 本身在 F4 就进来了：stage5 的 `must_appear_missing` 是它的第一个消费方，
本模块的 `_find_or_recover` 是第二个——**不许再写一份最长公共子串**（判据 25）。
"""
from __future__ import annotations

import re
from typing import Any, Iterator

#: 汉字/字母/数字；标点与空白都不算内容。⚠ 见模块 docstring：**别拿它去换 `text.norm`**。
CONTENT = re.compile(r'\w')

#: 按最长公共子串救锚点时的**相对**门槛：覆盖率低于这个值就不猜，报「找不到」。
#:
#: 【它今天只有一个用户，那不是漏接】原型这个常数曾经有两个消费方（span 锚点与 hinge），
#: 而 **hinges 那一侧的 LCS 兜底已被整个删掉**：实测「传家玉镯」→「家传玉镯」的
#: 最长公共子串只剩「玉镯」**2/4**，救回来的是一个**语义已经变了**的片段。
#: ⇒ 搬进本仓时**只接 span 锚点这一路**，不许顺手给 hinge 也接一条
#: （`hinge_drift` / `hinge_anchor_void` 要的正是「位置对、字节错」这件事被报出来）。
HINGE_LCS_MIN = 0.8

#: 救回来的片段还要够长的**绝对**门槛。两道门是**与**关系：
#: 只有相对门槛时，`'的我'` 这种两字锚点用一个字就能凑到 0.5 以上；
#: 只有绝对门槛时，一个 20 字锚点里对上 4 个字也算救回来了。
_RECOVER_MIN_CHARS = 4


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


def _find_or_recover(content: str, needle: str, start: int,
                     recover: bool) -> tuple[int, str]:
    """在 `content[start:]` 里找 `needle`；找不到且 `recover=True` 时按最长公共子串**救一次**。

    **逐字移植自原型 `Stage2/stage2_common.py::_find_or_recover`，注释一起搬。**

    救的是同一类反复出现的损坏：模型在照抄锚点时改掉一两个字
    （原型实测 5 次：`'都不会善罢甘休'` ← 原文 `'定然不会善罢甘休'`；
    `'难产的对我说过'` ← `'难产的我说过'`）。
    **它是原文片段字段，就该按原文片段的办法修。**

    ⚠⚠ **只在落盘时（`normalize_verbatim`）开 `recover`，校验器一律 `recover=False`**：
    否则「能定位」会把「锚点不是原文字节」这件事盖掉——而那正是
    `anchor_not_verbatim` 与 `_provenance.verbatim_recovered` 要说的话。

    返回 `(命中下标, **实际匹配到的串**)`；未命中返回 `(-1, needle)`。
    ⚠ 第二格必须被调用方**接回去**：救回来的片段长度与 `needle` 不同，
    而后面的区间算术全用它的 `len()`——不接就是一串错位的原文偏移，**且不会报错**。
    """
    i = content.find(needle, start)
    if i >= 0 or not recover:
        return i, needle
    i0, n = lcs(needle, content[start:])
    if n >= _RECOVER_MIN_CHARS and n / len(needle) >= HINGE_LCS_MIN:
        frag = needle[i0:i0 + n]
        j = content.find(frag, start)
        if j >= 0:
            return j, frag
    return -1, needle


def extend_punct(text: str, end: int) -> int:
    """把区间末尾延到**紧邻的标点**（不跨空白/换行）。

    内容空间的命中区间止于最后一个**内容字符**，句末的「。」「！」会落在区间外。
    对定位无所谓，但对**回取原文字节**有所谓：不延就等于把原文的句末标点削掉，
    而 hinge 是要逐字发给下游的。
    ⚠ **不跨空白**：原文是「每句一段」排版，跨过去就吃进了下一句。
    """
    while end < len(text) and not CONTENT.match(text[end]) and not text[end].isspace():
        end += 1
    return end


_TERMINAL_SUFFIX = frozenset('\u3002\uff01\uff1f?!\u2026.')


def extend_terminal_punct(text: str, end: int) -> int:
    """只接住句末闭合标点，不把冒号、开引号等下一段语义吞进材料。"""
    while end < len(text) and text[end] in _TERMINAL_SUFFIX:
        end += 1
    return end


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
    if source.get('normalized') != content_only(raw):
        return 0, 0, 'source span normalized text does not match'
    if source.get('recovery') not in ('exact', 'normalized', 'lcs'):
        return 0, 0, 'source span recovery is invalid'
    return start, end, None


def walk_spans(bps: list[dict[str, Any]],
               text_of: dict[Any, str],
               *, recover: bool = False) -> Iterator[tuple[Any, dict, tuple[int, int, int, int] | None, str | None]]:
    """把 `beat.span` 的首尾锚点在原文里逐条定位——**【唯一】一份游标实现**。

    两个视图共用它：`locate_spans()` 要的是 beat 的**整段区间**（量纲），
    stage2 的落盘路要的是两个锚点**各自**的区间（回取原文字节）。
    各写一份游标就是下一次漂移的源头。

    **游标单调**：`starts_with` 只在上一条终点之后找，`ends_with` 只在本条起点之后找。
    这样既防止匹配到前文的同形句，也顺带把「顺序倒置」表达成「找不到」——**两件事一个机制**。

    逐条 yield `(chapter_ordinal, beat, hit, problem)`：
      · 命中 → `hit = (a0, a1, b0, b1)` **原文**偏移，`starts_with=[a0,a1)`、`ends_with=[b0,b1)`；
        `problem=None`
      · 失败 → `hit=None`，`problem` = 说明。**定位不到就报出来，绝不退回估算**
        （估算方向会整个反过来）。

    ⚠⚠ **`recover` 是 keyword-only 且默认 `False`，而那个默认值是本模块的一条不变量**
    （见模块 docstring）：**只有 stage2 的落盘路许开它**。
    开着跑校验，「能定位」就会把「锚点不是原文字节」这件事盖掉。
    """
    for x in bps:
        o = x.get('chapter_ordinal')
        text = text_of.get(o, '')
        content, cmap = content_index(text)
        cur = 0                                # cur 是【内容空间】的游标
        raw_cur = 0
        for be in (x.get('beats') or []):
            sp = be.get('span') or {}
            if any(key in sp for key in ('start', 'end', 'text', 'normalized')):
                start, end, problem = exact_span(sp, text, raw_cur)
                if problem:
                    bid = be.get('beat_id')
                    yield o, be, None, f'{bid}: {problem}'
                else:
                    yield o, be, (start, start, end, end), None
                    raw_cur = end
                continue
            raw_a = (sp.get('starts_with') or '').strip()
            raw_b = (sp.get('ends_with') or '').strip()
            bid = be.get('beat_id')
            if not raw_a or not raw_b:
                yield o, be, None, f'{bid} 缺 span 锚点'
                continue
            a, b = content_only(raw_a), content_only(raw_b)
            if not a or not b:
                yield o, be, None, f'{bid} 锚点无内容字符: {raw_a[:12]!r}/{raw_b[:12]!r}'
                continue
            # ⚠ `a` 被**重新绑定**成实际匹配到的串（救回来时它比原锚点短）：
            #   下面每一处 `len(a)` 都是区间算术的输入，用原锚点的长度就是错位的偏移。
            i, a = _find_or_recover(content, a, cur, recover)
            if i < 0:
                # 【先分清是「原文里没有」还是「被上一条越过去了」】两者的病因完全不同，
                # 而说错了会让人去查一个**根本没问题**的锚点（判据 32）。
                # 实测 `b06_06`：锚点「这套黑皮衣」在 ch6 内容序 216 好好地在那儿，
                # 真正出问题的是 `b06_05` 的 `ends_with`「不知道放哪了」——它是**句子中间**的
                # 六个字，落进了 `b06_06` 起始句的内部，把游标推过了头。
                # 报「找不到」等于把上一条的越界说成这一条的锚点错。
                back = content.find(a)
                yield o, be, None, (
                    f'{bid}.starts_with 落在游标【之前】(内容序 {back} < {cur})：'
                    f'{raw_a[:14]!r} —— 锚点本身在原文里是对的，'
                    f'是**上一条 beat 的 span 伸进了这一条的开头**（区间重叠 / 顺序倒置）。'
                    f'契约：第 n 条的 starts_with 必须在第 n-1 条的 ends_with 之后。'
                    f'要修的是上一条的 ends_with，不是这一条的 starts_with'
                    if 0 <= back < cur else
                    f'{bid}.starts_with 在 ch{o} 第 {cur} 字(内容序)之后找不到: {raw_a[:14]!r}')
                continue
            j, b = _find_or_recover(content, b, i, recover)      # 同上：`b` 也要接回去
            if j < 0:
                yield o, be, None, f'{bid}.ends_with 在 starts_with 之后找不到: {raw_b[:14]!r}'
                continue
            # 两端都延到紧邻标点：区间要覆盖整句（含句末标点），否则 hinge 切片里就没有它
            yield o, be, (cmap[i], extend_punct(text, cmap[i + len(a) - 1] + 1),
                          cmap[j], extend_punct(text, cmap[j + len(b) - 1] + 1)), None
            cur = j + len(b)


def locate_spans(bps: list[dict[str, Any]],
                 text_of: dict[Any, str]) -> tuple[dict[Any, list[tuple[str, int, int]]], list[str], int]:
    """按 `beat.span` 确定性算出每条 beat 在原文里的字符区间（返回的是**原文**偏移）。

    返回 `(spans, problems, loose)`：
      · `spans` = `{chapter_ordinal: [(beat_id, start, end), …]}`
      · `problems` = 无法定位的锚点，**逐条点名**
      · `loose` = **归一化之后才命中**的条数

    ⚠ `loose` 不是统计口味：stage2 落盘时会把锚点回取成原文字节，跑过那一步之后
    它**应当恒为 0**——非 0 即「锚点被改过而未回取」。它是一条**会说话的读数**，
    别把它读成噪声。
    """
    spans: dict[Any, list[tuple[str, int, int]]] = {}
    problems: list[str] = []
    loose = 0
    for o, be, hit, prob in walk_spans(bps, text_of):
        if prob:
            problems.append(prob)
            continue
        assert hit is not None                      # `prob is None` ⇒ 必然命中
        a0, _a1, _b0, b1 = hit
        text = text_of.get(o, '')
        sp = be.get('span') or {}
        loose += sum(1 for f in ('starts_with', 'ends_with')      # 逐字比不上＝标点/空白被改
                     if (sp.get(f) or '').strip() not in text)
        spans.setdefault(o, []).append((be.get('beat_id'), a0, b1))
    return spans, problems, loose


def has_spans(bps: list[dict[str, Any]]) -> bool:
    """这份产物是不是按**带 span 的契约**产出的。

    旧产物没有 span ⇒ 量纲/保真相关的处理一律跳过，**而不是拿 0 当字数**。
    ⚠ 盘上三本 **251/251 条 beat 全部有 span**（93 · 67 · 91），所以这条今天恒真；
    留着它是因为「没有 span」与「span 定位不到」是两件事，前者连报都不该报。
    """
    return any((be or {}).get('span') for x in bps for be in (x.get('beats') or []))
