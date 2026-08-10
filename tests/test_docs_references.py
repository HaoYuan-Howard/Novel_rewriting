#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_docs_references.py —— 文档里的**引用**不许悬空。

【为什么要有】2026-08-10 的 stage1 审阅抓到五处问题，其中**两处是同一个形状**：
  · `stage1.md` §六 指着 `tests/fixtures_stage1.py`，而盘上叫 `fixtures_ingest.py`
  · `R-0.3` 拆成 `R-0.3a`/`R-0.3b` 之后，还有一处引用留在旧编号上

两处都**不会报错**——读的人会去找一个不存在的文件、或去查一条不存在的需求，
然后按自己的猜测把它读通。这正是判据 23 的文档版：
**读着已改名东西的消费方不会报错，它只是安静地什么也不做。**

本文件把这一类变成会红的测试。它查两件事：
  ① 文档里出现的每个 `R-x.y` / `Q-x` / `B-xx` 编号，在 `REQUIREMENTS.md` 里真的存在
  ② 文档里出现的每个**仓内路径**，盘上真的存在
"""
from __future__ import annotations

import os
import re

import pytest

from contracts import requirements as reqs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = reqs.load()

#: 扫哪些文档。`design_brief/` 不扫——它是给外部设计工具的包，用的是另一套编号（U-xx / S-xx）。
DOC_GLOBS = ('docs/schema/*.md', 'docs/REQUIREMENTS.md', 'README.md')

#: 只有这些前缀才当「仓内路径」看。`stage0/clean_text.txt` 这类是**产物路径**，不在仓里。
REPO_PREFIXES = ('tests/', 'contracts/', 'tools/', 'novel/', 'docs/')

_ID = re.compile(r'`(R-\d+\.\d+[a-z]?|Q-\d+|B-\d+)`')
_PATH = re.compile(r'`((?:tests|contracts|tools|novel|docs)/[A-Za-z0-9_./-]+)`')


def _docs() -> list[str]:
    import glob
    out: list[str] = []
    for pat in DOC_GLOBS:
        out += sorted(glob.glob(os.path.join(ROOT, pat)))
    return out


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, '/')


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_every_referenced_requirement_id_exists(doc):
    """引用一个不存在的需求编号 = 需求改过而文档没跟上。"""
    text = open(doc, encoding='utf-8').read()
    known = set(DOC.requirements)
    missing = sorted({m for m in _ID.findall(text) if m not in known})
    assert not missing, (
        f'{_rel(doc)} 引用了不存在的编号：{missing}\n'
        f'→ 需求改过（拆分/改名/删除）而这里没跟上。'
        f'改编号时要 grep 全部文档，不只是校验器'
    )


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_every_referenced_repo_path_exists(doc):
    """引用一个不存在的文件 = 读的人会去找它，然后按自己的猜测把文档读通。"""
    text = open(doc, encoding='utf-8').read()
    bad = []
    for path in sorted(set(_PATH.findall(text))):
        # 目录形（以 / 结尾）与文件形都接受
        full = os.path.join(ROOT, path)
        if not (os.path.exists(full) or os.path.exists(full.rstrip('/'))):
            bad.append(path)
    assert not bad, (
        f'{_rel(doc)} 引用了盘上不存在的路径：{bad}\n'
        f'→ 文件改名/搬家了而文档没跟上。这类引用不会报错，只会静默误导'
    )


def test_the_scan_actually_finds_something():
    """守卫自己的守卫：正则失配时上面两条会**全绿**，而那正是最坏的情况。

    「没报违规」与「根本没在查」必须能区分——这条测试自己也不例外。
    """
    ids = paths = 0
    for doc in _docs():
        text = open(doc, encoding='utf-8').read()
        ids += len(_ID.findall(text))
        paths += len(_PATH.findall(text))
    assert ids > 100, f'只扫到 {ids} 个编号引用——正则多半失配了'
    assert paths > 10, f'只扫到 {paths} 个路径引用——正则多半失配了'
