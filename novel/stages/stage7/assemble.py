#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assemble.py —— `stage7.assemble`：把替换后的文案与正文拼成 `stage7/book.txt`。

**它是交付物，不是审阅稿。** 里面没有地方写「缺章」抬头横幅
——所以四类残缺态**一律拒写**（`R-10.14`），而不是写一份带警告的 txt。

〔`R-10.8` 那一行「谁在查」标的代码落点：**`assemble_txt` 是唯一的组装路径**，
章分隔与换行都是常量，不是参数。〕
"""
from __future__ import annotations

from contracts.invariants import BOOK_BLOCK_SEP, BOOK_CHAPTER_SEP
from contracts.schema.report import CheckReport
from contracts.schema.stage7 import BookTxt, NameMap
from novel.errors import TerminalState, UpstreamMissing
from novel.run import RunContext
from novel.stages.stage7.rename import (
    BOOK_TXT, REL, _Upstreams, _judge, _report,
)
from novel.stages.stage7.subs import apply_subs, sub_table
from novel.store import ArtifactStore


def assemble_txt(hook_text: str, recs: list[dict], subs: dict[str, str],
                 kept: list[str] | tuple[str, ...] | set[str]) -> str:
    """替换后的文案 + 各章正文 → `book.txt` 的**全部字节**。

    形态：`文案 \\n\\n 1 \\n\\n 第1章正文 \\n\\n 2 \\n\\n …` + 末尾一个换行。
    **不写「第 N 章」**，章分隔就是一个裸阿拉伯数字单独成行；
    **没有任何审阅元信息**（不写书名、不写章标题、不写重合度）。

    ⚠ 换行只有 LF：写盘走 `store.write_text`，它按字节写并且**拿到 `\\r` 一律拒收**
    （归一是内容决策，属产地；写盘口只保证平台不掺东西）。
    ⚠ 原子落盘也在写盘口——并发读者读不到半截文件。
    """
    pat, _table = sub_table(subs, kept)
    parts: list[str] = []
    if hook_text:
        parts.append(hook_text.strip())
    for r in recs:
        body, _cnt, _claimed = apply_subs(r.get('text') or '', subs, pat, kept)
        parts.append(BOOK_CHAPTER_SEP.format(r.get('output_ordinal')))
        parts.append(body.strip())
    return BOOK_BLOCK_SEP.join(parts) + '\n'


def run_assemble(ctx: RunContext) -> tuple[str, NameMap, CheckReport]:
    """`stage7.assemble`：定稿之后组装成书。**0 成本，写盘。**

    ⚠⚠ **四类残缺态在这里硬拦，而不在 `measure.collect_chapters` 里。**
    那个函数会被复检经 HTTP 工作线程调用，而 `SystemExit` 是 `BaseException`
    ——一次「这本书还没写完」就会杀掉整个服务。**这条是付过一次代价的。**
    这里是 CLI 的调用点，抛的是 `TerminalState`（显式终态，不打栈）。

    ⚠ 「文案未定稿」也是四类之一，而**正因为这道闸挡在前面**，
    「成书里没有开篇文案」这一态**结构上不可达** ⇒ `book_txt.has_hook` 已按待决④ 裁掉。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage7.rename')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(NameMap, REL)

    bad: list[str] = []
    if up.dup:
        bad.append(f'同章双份：{up.dup}（哪一份是成品无从判断，删掉不要的那一份）')
    if up.missing:
        bad.append(f'计划有而成稿无：{up.missing}（先把它们写出来）')
    if up.mixed:
        bad.append(f'旧契约稿：{up.mixed}（重写它们，否则拼出来不是一本连贯的书）')
    if not up.hook_text:
        bad.append('开篇文案还没定稿（txt 的第一段就是它：先跑 `stage6.approve --pick N`）')
    if bad:
        raise TerminalState(
            '成书是交付物，不是审阅稿——txt 里没有地方写「缺章」那种抬头横幅：\n    '
            + '\n    '.join(bad))

    subs = dict(art.subs)
    kept = list(art.kept)
    hook_new, _cnt, _claimed = apply_subs(up.hook_text, subs, None, kept)
    text = assemble_txt(hook_new, up.recs, subs, kept)
    p = store.write_text(BOOK_TXT, text)

    from novel import prov
    new = art.model_copy(update={
        'book_txt': BookTxt(sha1=prov.content_sha1(text.encode('utf-8')),
                            # ⚠ **全链第三个 `chars` 口径**：含空白，**并且含章号行与
                            #   全部块间分隔符**。别拿它和另外两个 `chars` 做任何比较。
                            chars=len(text), chapters=len(up.recs)),
        # ⚠ **`_provenance` 一个字不动**：组装没有改任何 LLM 产的字节，
        #   它只是把已有的映射应用了一遍并把交付物的账记下来。
    })
    store.write(REL, new)
    return str(p), new, _report(ctx, store, _judge(ctx, store, new, up), new)
