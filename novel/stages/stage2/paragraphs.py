#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paragraphs.py —— 章正文的**段落编号表**：下发、回取、反投影共用的那一份。

【它为什么存在】`stage2.blueprint` 的定位方式是「下发带编号的段落、模型只交段号」。
这件事有三个消费方，而它们必须用**同一张表**：

  · 下发侧  `extract.build_blueprint_user`   —— 把当前章正文渲染成 `[n] …`
  · 回取侧  `normalize.normalize_verbatim`   —— 把段号换回 `SourceSpan` 的字符区间
  · 反投影  `normalize.response_shape`       —— 把已有区间反算回段号（定向重生成的基线）

三边各切一次段，就是同一件事的三个落点：渲染时把某一行算成第 12 段、回取时算成第 13 段，
产出的是**一段错位的原文偏移，而它不会报错**——只会让某几拍指向别的地方
（同形的先例见 `novel/core/spans.py` 模块头那条 `cmap` 下标映射的警告）。
⇒ `render_numbered` 的编号**由 `paragraph_spans` 派生**，不许自己再 `split` 一次。

⚠⚠ **不要拿 `novel/core/style.py::paragraphs` 来用。** 那一份 `.strip()` 每行并丢弃空行，
返回的是**文本**不是区间 —— 偏移映射当场就没了，而 `SourceSpan.start/end` 要的正是偏移。
两份各留各的：那边量的是成稿的段落形态，这边管的是源章的坐标。

⚠ **也不走 `novel/core/spans.py::content_index` 那把尺子。** 段落切分只认换行，
与「内容字符空间」是两回事；那两个正则的分歧是已登记的（`tests/test_core.py` 钉着），
在这里混用会把标点密集的段算错边界。

【标记为什么是 `<n>` 而不是更眼熟的 `[n]`】盘上三本书 36 章 39,236 字实测，
**这三种括号在正文里全都是真内容**：`[` `]` 各 127 次（本语料把台词引号写成
`[世子跟谢姑娘真是心有灵犀]`）、`【` `】` 各 46 次、`「` `」` 255/256 次。
拿它们当编号标记，模型要在「这是第几段」与「这是一句台词」之间猜。
`<` `>` 在同一批语料上是 **0 次**，且是 ASCII（比全角括号省 token）。
⚠ 真正的不变量是**「行首 + 空格分隔」**，不是这个字符本身：代码从不解析模型回写的文本，
模型只交整数。换字符时把上面这批计数重测一遍即可，别只换不测。

【口径】
  · 一段 = `text.split('\\n')` 里 `strip()` 之后非空的一行；空行只是分隔，不占编号。
  · 段号**从 1 起、逐章独立**（与 `chapter_ordinal` 一样是给人看的序，不是下标）。
  · 区间是**章内 Python 码点半开区间** `[start, end)`，与 `SourceSpan.start/end` 同一空间；
    **不含行尾换行**，也不 strip 行内空白（`SourceSpan.text` 必须逐字等于 `text[start:end]`）。
  · 多段区间 `span_of(a, b)` = 从第 a 段的**起点**到第 b 段的**终点**，
    中间的换行与空行**含在内** —— 这样相邻两拍的区间首尾相接，
    `normalized`（`content_only`）会把分隔符滤掉，不影响判据。
"""
from __future__ import annotations

#: 一段在章正文里的半开区间 `[start, end)`。
Paragraph = tuple[int, int]

#: 下发块里每一行的形状。⚠ **只有这一个落点**——prompt 里那句「每行以 `<数字>` 开头」
#: 说的就是它，两边分家就是在教模型认一个我们没发出去的格式。
LINE_FORMAT = '<{n}> {text}'


class ParagraphIndexError(ValueError):
    """段号解析不出区间。**消息即失败根因**，由 `normalize` 原样记进
    `_provenance.verbatim_failures`——所以措辞要说得出「该修哪里」。
    """


def paragraph_spans(text: str) -> list[Paragraph]:
    """章正文 → 逐段的半开区间，**按阅读顺序**。

    ⚠ 游标按 `len(line) + 1` 前进（`+1` 是被 `split` 吃掉的那个 `\\n`），
    因此空行也照常推进游标 —— 它不占编号，但占坐标。
    """
    out: list[Paragraph] = []
    pos = 0
    for line in text.split('\n'):
        if line.strip():
            out.append((pos, pos + len(line)))
        pos += len(line) + 1
    return out


def render_numbered(text: str) -> str:
    """章正文 → 下发给模型的 `<n> …` 编号块。

    ⚠⚠ **编号与区间同源**：这里逐字取 `paragraph_spans` 切出来的那一段
    （`text[s:e]`），不再 `split` 第二次。两边分家的后果是静默错位，见模块头。
    ⚠ 空行**不输出**：它在原文里只是排版分隔，下发它等于给模型一堆没有编号的空行，
    而模型交回的段号是按看到的顺序数的。
    """
    return '\n'.join(LINE_FORMAT.format(n=n, text=text[s:e])
                     for n, (s, e) in enumerate(paragraph_spans(text), start=1))


def span_of(text: str, start_para: int, end_para: int) -> tuple[int, int]:
    """段号**闭区间**（1-based）→ 章内字符半开区间。

    定位不到就抛，**绝不退回估算**——估算方向会整个反过来
    （与 `novel/core/spans.py::exact_span` 同一条约定：报得出问题，不猜一个偏移）。
    """
    spans = paragraph_spans(text)
    total = len(spans)
    if type(start_para) is not int or type(end_para) is not int:
        raise ParagraphIndexError(
            f'段号不是整数：start_para={start_para!r} end_para={end_para!r}')
    if not 1 <= start_para <= total or not 1 <= end_para <= total:
        raise ParagraphIndexError(
            f'段号越界 [{start_para}:{end_para}]，本章只有 {total} 段')
    if end_para < start_para:
        raise ParagraphIndexError(
            f'段号区间倒序 [{start_para}:{end_para}]')
    return spans[start_para - 1][0], spans[end_para - 1][1]


def paragraph_at(text: str, offset: int) -> int | None:
    """哪一段**包含**这个偏移；落在空行/换行这类段间空隙里返回 `None`。"""
    for n, (s, e) in enumerate(paragraph_spans(text), start=1):
        if s <= offset < e:
            return n
    return None


def paragraph_range_of(text: str, start: int, end: int) -> tuple[int, int]:
    """字符区间 → **覆盖它的最小段号闭区间**（`response_shape` 的反投影）。

    ⚠ 用「相交」而不是「包含」：正式区间的两端本来就可能落在段内
    （旧形状的锚点是句子级的），按包含算会把首尾两段整个丢掉，
    反喂给模型的基线就比原来窄——而那份基线的用途正是「不许减少已有内容」。
    """
    spans = paragraph_spans(text)
    hit = [n for n, (s, e) in enumerate(spans, start=1) if s < end and start < e]
    if not hit:
        raise ParagraphIndexError(
            f'区间 [{start}:{end}] 不与任何段落相交（本章 {len(spans)} 段）')
    return hit[0], hit[-1]


def paragraph_slice(text: str, para: int) -> tuple[int, int]:
    """单段的区间。hinge 的段内定位用它——搜索面从整章缩到这一段。"""
    spans = paragraph_spans(text)
    if type(para) is not int or not 1 <= para <= len(spans):
        raise ParagraphIndexError(
            f'段号 {para!r} 越界，本章只有 {len(spans)} 段')
    return spans[para - 1]
