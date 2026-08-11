#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
facts.py —— 中文正文里的**数值词法**与 secret 的**关键片段切法**。
**逐字移植自原型（`Stage2/stage2_common.py`），注释一起搬。**

两样东西都是「拿文本做字面匹配」的**唯一落点**，各有多个消费方：
  · `cn_num_pattern` / `NUM_UNIT` —— stage5 的数值守恒 · stage6 的「发明数字」探针；
  · `secret_keywords` —— stage4 的下发面闸门 · stage5 的提前点破 · stage6 的秘密记账。

各处自己抄一份切法必然漂，而漂的表现是**其中一处悄悄不再拦**（判据 25）。
"""
from __future__ import annotations

import re

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
