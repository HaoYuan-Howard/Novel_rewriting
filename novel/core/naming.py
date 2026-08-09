#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naming.py —— 目录寻址：**全仓唯一一把尺子**。

【为什么必须只有一份】产地与消费方各写一份，结果不是报错，是**产物散进两个目录、
而没有任何一层会出声**。原型实测过：界面自己写了一份「exact 或 glob `*_sid`」的匹配逻辑，
随后就与共享层分了家（共享层加的 `glob.escape` 那边没有），于是书名里带 `[` 的书在两处
指向不同的目录。规则留一份，「找不到怎么办」交给调用方。

⚠ **原样移植自快速原型，连注释一起搬**（需求 R-1.10）。注释里写的是
「为什么不能用那个更直观的写法」——那些才是踩出来的东西。
"""
from __future__ import annotations

import glob
import os
import re

_SOURCE_EXT = re.compile(r'\.(?:txt|md)$', re.I)
_UNSAFE_TITLE = re.compile(r'[\\/:*?"<>|]')
_CTRL = re.compile(r'[\x00-\x1f\x7f]')
MAX_TITLE = 80


def derive_source_meta(path_or_name: str) -> tuple[str, str, str | None]:
    """`<platform?>_<title>[_<id>]` → `(source_id, title, platform)`。

    **两步解析，不用一条带可选组的正则**：`book_123` 在
    `^(?:([A-Za-z]+)_)?(.+?)(?:_(\\d+))?$` 下会被贪心的 platform 组读成
    `(platform='book', title='123')`——可选组的歧义不会报错，它只会在某些名字上悄悄换一个答案。
    先从尾部锚 id，再从头部剥平台前缀，就没有歧义可言。

    **没有 `_<数字>` 尾巴时 `source_id = title`**（语料里 13 本这样，如 `我的妻子.txt`）：
    书名本身就是目录键。旧实现在这里返回 `(整个文件名, 整个文件名)`，于是目录会建成
    `我的妻子_我的妻子`——能跑，但键空间里多了一个没人想要的形态。
    """
    name = _SOURCE_EXT.sub('', os.path.basename(path_or_name))
    m = re.search(r'_(\d+)$', name)
    sid, rest = (m.group(1), name[:m.start()]) if m else (None, name)
    pm = re.match(r'^([A-Za-z]+)_', rest)
    # 平台前缀只在【剥完还剩东西】时才算前缀，否则 `FQ_` 这种名字会被剥成空标题
    if pm and rest[pm.end():]:
        platform, title = pm.group(1), rest[pm.end():]
    else:
        platform, title = None, rest
    return (sid or title), title, platform


def safe_title(title: str) -> str:
    """书名 → 文件系统安全的目录名片段。清洗到**不动点**，写盘前由 round-trip 断言把关。"""
    t = _CTRL.sub('', title or '')
    t = _UNSAFE_TITLE.sub('_', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = t.rstrip(' .')                       # Windows 不接受以点/空格结尾的目录名
    return t[:MAX_TITLE].rstrip(' .')


def novel_dirname(title: str, sid: str) -> str:
    """`(标题, id)` → per-novel 目录名。无 id 的书（`sid == title`）目录就叫书名本身。

    **必须先把标题剥到不动点**：`Test_book` 拼成 `Test_book_<id>` 之后，这把尺子会把
    `Test_` 当平台前缀吃掉、解析出标题 `book`——于是产地建的是 `Test_book_<id>/`、
    消费方找的是 `book_<id>/`，同一本书裂成两个目录，而没有任何一层会报错。
    所以这里**先替尺子剥一遍**，让目录建在它待会儿要读的地方。
    """
    t = safe_title(title)
    for _ in range(8):
        cand = t if sid == t else f'{t}_{sid}'
        _sid2, t2, plat = derive_source_meta(cand)
        if plat is None and t2 == t:
            return cand
        if not t2 or t2 == t:
            break                                # 不收敛，交给 round-trip 断言拦下
        t = t2
    return t if sid == t else f'{t}_{sid}'


def dirname_roundtrip_errors(title: str, sid: str) -> list[str]:
    """写盘【之前】检查目录名能不能被这把尺子原样解析回来；返回错误串列表（空 = 通过）。

    这条断言是本模块唯一的正确性保证：清洗与剥前缀再周全也是启发式，
    而「解析不回去」的后果是产物散进两个目录、且没有任何一层会报错。
    **本函数只报告、不终止**——校验器也要用它，不能被上游缺失打断。
    """
    d = novel_dirname(title, sid)
    got_sid, got_title, got_plat = derive_source_meta(d)
    errs = []
    if got_sid != sid or got_plat is not None:
        errs.append(f'目录名 {d!r} 解析回 (id={got_sid!r}, 标题={got_title!r}, 平台={got_plat!r})，'
                    f'期望 id={sid!r} 且无平台前缀——下游所有节点会找不到这本书')
    elif novel_dirname(got_title, got_sid) != d:
        errs.append(f'目录名 {d!r} 不是不动点（再解析再建名得到 '
                    f'{novel_dirname(got_title, got_sid)!r}）——同一本书会裂成两个目录')
    return errs


def find_novel_dir(base: str, sid: str) -> str | None:
    """按不可变 id 定位 per-novel 文件夹：`<base>/<id>/` 或 `<base>/<*_id>/`。**找不到返回 None。**

    `glob.escape`：sid 现在可能是书名（无 id 的书），而书名里的 `[`/`]` 在 glob 里是字符类。

    【为什么是「返回 None」而不是终止】对 node 来说「上游没跑就该停」是对的，
    对**长驻进程**却是致命的：一次「这本书还没跑 stage1」会杀掉整个服务。
    规则留一份，「找不到怎么办」交给调用方。
    """
    exact = os.path.join(base, str(sid))
    cands = ([exact] if os.path.isdir(exact) else []) \
        + sorted(glob.glob(os.path.join(glob.escape(base), f'*_{glob.escape(str(sid))}')))
    return next((c for c in cands if os.path.isdir(c)), None)
