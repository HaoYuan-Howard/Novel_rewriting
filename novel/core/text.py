#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text.py —— 文本层的正则与归一。**逐字移植自原型，注释一起搬。**

这几条正则每一条都带着一次实测的账，改之前先读注释。
"""
from __future__ import annotations

import re

from contracts.invariants import SPEECH_TAG_MAX

#: 句界：**必须含【半角 `?!`】**——stage1 原文标点不统一，半角占比高的书
#: （普女 82 处 / 清杳 90 处）若只认全角，句数被系统性少算、均句长被高估
#: （普女 22.7→19.0、清杳 17.2→14.9），而这些数字是喂给抽取器的「权威事实」，
#: 错了会直接把定性描述带偏。
SENT_END = re.compile(r'[。！？!?…]+')

#: 引号体：按**实际成对跨度**判定，不按单字符出现——孤立一个引号不算对白体。
QUOTE_STYLES = (('「」', re.compile(r'「[^」]*」')),
                ('“”', re.compile(r'“[^”]*”')),
                ('『』', re.compile(r'『[^』]*』')),
                ('""', re.compile(r'"[^"]*"')))

#: 取引号内片段（举例用的原文微引用）。上界 40 字是为了不把整段吞进来。
QUOTED = re.compile(r'「([^」]{1,40})」|“([^”]{1,40})”|‘([^’]{1,40})’|'
                    r'『([^』]{1,40})』|"([^"]{1,40})"')

#: 「结尾必须有终止标点」的那套。截断判定与删减守卫共用这一份——
#: 两处各抄一套就会漂（判据 25）。
# 半角/全角方括号是界面消息的闭合符。Stage 5 真实写作会稳定产出
# ``[提示内容。]`` / ``［提示内容。］``；它们与 ``【提示内容。】`` 同形，不能被
# “疑似截断”误判。这里只收闭合符，不收左括号。
TERMINAL_PUNCT = '。！？…”」』】]］!?.'

_NON_CONTENT = re.compile(r'[^一-鿿A-Za-z0-9]')


def norm(s: str | None) -> str:
    """内容字符归一：去标点/空白/表情，只留中日韩汉字与字母数字。

    用于与原文比对——**LLM 常改写标点**（半角逗号→全角），不归一会漏掉真摘抄。
    """
    return _NON_CONTENT.sub('', s or '')


def norm_index(text: str | None) -> tuple[str, list[int]]:
    """`(归一串, 归一下标 → 原文下标)`。**回映到原文字节的唯一入口。**

    `locate_norm` 与 `split_by_speech_tag` 共用它——两处各建一份索引就是同一把尺子的
    第二个落点，而它们比的是**同一段正文**（判据 14）。
    """
    t = text or ''
    idx = [i for i, ch in enumerate(t) if not _NON_CONTENT.match(ch)]
    return ''.join(t[i] for i in idx), idx


def locate_norm(needle: str | None, text: str | None) -> tuple[int, int] | None:
    """在 `text` 里定位一段**归一后**的串 → 原文下标区间 `(start, end)`；找不到返回 `None`。

    ⚠⚠ **这个回映是承重的，不是便利函数。** `norm()` 剥掉标点与空白，所以
    「归一后匹配得上」**不等于**「原样出现过」。凡是要把一条结论的凭据落回
    产物**自己的字节**，就必须走这条回映：

      · 剧透/秘密检测命中的片段来自 `secret.fact`（**不是产物**），
        直接拿它当凭据展示，等于把那条秘密原文抄进报告——**护栏自己成了泄漏源**。
        回映之后引用的是**产物自己写过的那句话**，一个新字节都不多给。
      · 界面要在右栏把凭据高亮出来，也只能用原文下标，**且必须服务端算**：
        JS 是 UTF-16 码元、Python 是码点，出现一个 emoji 之后全部错位。
    """
    k = norm(needle)
    if not k:
        return None
    hay, idx = norm_index(text)
    p = hay.find(k)
    if p < 0:
        return None
    return idx[p], idx[p + len(k) - 1] + 1


def whitespace_equivalent_slice(needle: str, body: str) -> str | None:
    """唯一匹配时，把仅空白不同的模型 evidence 回取成正文原字节。

    标点、汉字和顺序一概不归一；相同内容在正文出现两次时也拒绝猜位置。返回值因此
    仍是 ``body`` 的真实连续子串，后续引用不需要另一套“近似证据”语义。

    ⚠ 两个语义审判共用这一份（stage5 逐章 claims · stage6 文案 claims）——
    各抄一份回取逻辑就会漂，而漂的表现是其中一处悄悄开始收下拼接证据。
    """
    compact_needle = ''.join(char for char in needle if not char.isspace())
    if not compact_needle:
        return None
    positions = [index for index, char in enumerate(body) if not char.isspace()]
    compact_body = ''.join(body[index] for index in positions)
    start = compact_body.find(compact_needle)
    if start < 0 or compact_body.find(compact_needle, start + 1) >= 0:
        return None
    end_compact = start + len(compact_needle) - 1
    if end_compact >= len(positions):
        return None
    recovered = body[positions[start]:positions[end_compact] + 1]
    if ''.join(char for char in recovered if not char.isspace()) != compact_needle:
        return None
    return recovered


def window_overlap(text: str | None, source: str | None, window: int = 20,
                   exclude: str = '') -> float | None:
    """两段文本的【N 字窗口重合率】，扣除 `exclude` 里允许原样出现的那部分。

    在 `norm` 空间里算（去标点/空白），防标点改写绕过。
    ⚠ `text` 短于一个窗口时返回 **`None`**——那不是「重合 0」，是**量不了**，
    两者必须分得开（判据 7）。

    **这里只有算法，没有口径**：跟谁比、什么算「允许重合」由调用方给。
    两套口径共用它——stage5 的成稿 vs 源章（扣 verbatim）· stage6 的文案 vs
    原作文案段（**无豁免**：文案里一句逐字要求都没有）。
    各自抄一份窗口逻辑就会漂，而漂的表现是其中一处悄悄不再拦（判据 25）。
    """
    src, t, ex = norm(source), norm(text), norm(exclude)
    if len(t) < window or not src:
        return None
    n = len(t) - window + 1
    hits = sum(1 for i in range(n)
               if t[i:i + window] in src and t[i:i + window] not in ex)
    return round(hits / n, 3)


def quotes(s: str | None) -> list[str]:
    """取出字符串里被引号包住的片段（举例用的原文微引用）。"""
    return [g for m in QUOTED.finditer(s or '') for g in m.groups() if g]


def quote_count(s: str | None) -> int:
    """直接引语的**条数**（四种引号体的成对跨度合计）。

    ⚠ 与 `measure().dialogue_ratio` 不是一回事，两者都要：那一个量的是**占多少字**，
    这一个量的是**说了几句**。台词被压成转述时前者可以纹丝不动
    （叙述里复述一遍同样长），后者掉一半——原型正是靠它抓到「闭卷把台词压成转述」。
    ⚠ 归一之后全书只剩目标引号体，但探针**对归一前后都要成立**（存量稿是归一后的，
    模型刚吐出来那一份不是）⇒ 四种体全表都数。
    """
    return sum(len(rx.findall(s or '')) for _, rx in QUOTE_STYLES)


# ── 「原话被一处提示语切开」也算逐字出现（`R-7.3b`）─────────────────────────
#
# 中文小说的标准写法：`「…，」我说，「…」`。**这是一个合法写法，不是违规**，
# 而按「原样出现」的字面判据它一定判缺失。⇒ 放行，但**单独留痕**：
# 「一个字都没动」与「切成两截后逐字出现」不是同一件事（判据 7）。
#
# ⚠ 插入必须在**原文字节**上长成「收引号 + ≤N 内容字 + 开引号」——
# 改词、插一段动作描写、只写半句照旧判缺失。这条正则就是那个形状。
_CLOSE_THEN_OPEN = re.compile(r'[」』”’][^」』”’「『“‘]{0,%d}[「『“‘]' % (SPEECH_TAG_MAX * 3))

#: 切口两侧各自至少要有几个内容字。**更短的碎片会在长正文里随便撞上**，
#: 那就成了「把判据放宽到没有意义」，而不是「放行一个合法写法」。
SPLIT_MIN_SIDE = 4


def split_by_speech_tag(seg_norm: str, body: str,
                        nb: str | None = None,
                        nmap: list[int] | None = None) -> str | None:
    """这条原话是不是**只被一处提示语切开**地出现在 `body` 里？→ 命中处的原文提示语。

    `seg_norm` 是**已归一**的待查串；`nb`/`nmap` 是 `norm_index(body)` 的结果，
    调用方一章查几十条时传进来复用（这函数在最内层循环里）。
    """
    if nb is None or nmap is None:
        nb, nmap = norm_index(body)
    n = len(seg_norm)
    if n < SPLIT_MIN_SIDE * 2:
        return None
    for i in range(SPLIT_MIN_SIDE, n - SPLIT_MIN_SIDE + 1):
        head, tail = seg_norm[:i], seg_norm[i:]
        p = nb.find(head)
        while p >= 0:
            q = nb.find(tail, p + i)
            if q < 0:
                break
            if q - (p + i) <= SPEECH_TAG_MAX:
                gap = body[nmap[p + i - 1] + 1:nmap[q]]
                if _CLOSE_THEN_OPEN.search(gap):
                    return gap
            p = nb.find(head, p + 1)
    return None


def min_hamming(needle: str, hay: str, cap: int = 2) -> int:
    """`needle` 与 `hay` 中**等长窗口**的最小逐字差异数（`cap` 以上不再细算）。

    用来区分「写手漏写了这句」与「写手只改了一两个字」——后者在实测里
    多数是**纠正原文的错别字**，那与漏写是两件事，报出来的话也不同。
    ⚠ **等长窗口而不是编辑距离**：要查的就是「原样」，长度都不一样就不必谈一字之差了。
    """
    n = len(needle)
    if n == 0 or len(hay) < n:
        return cap + 1
    best = cap + 1
    for i in range(len(hay) - n + 1):
        d = 0
        for a, b in zip(needle, hay[i:i + n]):
            if a != b:
                d += 1
                if d >= best:
                    break
        if d < best:
            best = d
            if best == 0:
                break
    return best


def render_book(main_text: object) -> tuple[str, str]:
    """正文列表 → `(按阅读序分块的全书正文, 章节索引)`。**只含正文，绝不含文案段。**

    ⚠ 章的唯一句柄是 `chapter_ordinal`（**修正后的阅读序**）；
    `source_chapter_no` 是原文标号，**参考、不可信**——全语料 28/163 本有重号、
    12/163 本有缺号，所以它只能标成「参考」发下去，不能当句柄。
    ⚠ 它 2026-08-22 从 `source_chapter_label: str` 改型成 `int`，而**渲染字节一个没变**：
      两处都是 `f'…={x}'`，`str(1) == '1'` ⇒ f-string 出来的字节完全相同。
      这不是「大概不会变」，`tests/test_stage01.py` 有一条专门钉它的用例。
    ⚠ 上游曾有第二个机器身份 `section_id`（只在 stage1、不进语义层）。它已随
      「一份产物只留一个系统主身份」删除，全链的章唯一键从此只有 `chapter_ordinal`。

    ⚠⚠ **这两个串逐字进 prompt**，所以它们的措辞是契约的一部分：
    改一个字，全部存量产物的 `_provenance.prompts` 指纹当场对不上、被报成 stale
    ——那是一次**假回归**（判据 28）。
    """
    parts, idx = [], []
    for m in main_text.root:            # type: ignore[attr-defined]
        ordi, lbl = m.chapter_ordinal, m.source_chapter_no
        idx.append(f'阅读序第{ordi}章  原文标号(参考,不可信)={lbl}')
        parts.append(f'===== [阅读序第{ordi}章 | 原文标号={lbl}] =====\n{m.text}')
    return '\n\n'.join(parts), '\n'.join(idx)
