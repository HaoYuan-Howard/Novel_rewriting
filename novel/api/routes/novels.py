#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novels.py —— 语料库与单书总览。**两个都是纯读端点。**

⚠ 三个响应模型里**一个 `badge` / `passed` / `counts` 都没有**（`R-11.4`）：
这一屏的结论只能来自 `/api/novels/:sid/check/:kind`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import NovelList
from contracts.schema.orchestration import NovelOverview
from novel import paths
from novel.api.deps import checked_sid, get_base
from novel.orchestrator.state import overview

router = APIRouter(prefix='/novels')


@router.get('', response_model=NovelList)
def list_novels(base: str = Depends(get_base)) -> NovelList:
    """`<base>` 底下有哪几本书，各自跑到哪了。

    ⚠ **复用 `NovelOverview`，不另造一张轻量清单。** 原型 `Web/novels.py` 那张
    第二清单的代价是具体的：stage5 的产物是逐章的、存在性不能靠一次 `is_file()`，
    于是它被**整个排除在外**——「有没有陈旧产物」那个徽章从此不覆盖成稿。
    ⚠ 已知代价：每本书都要跑一遍完成度（读上游算指纹）。盘上 3 本无压力，
    全语料 163 本会明显慢。**登记不优化**——要优化就给 `overview()` 加浅层分支。

    ⚠ **一本坏掉不许带走整屏。** 一个 id 匹配到两个目录（F-088）会让 `overview()`
    抛 `PathRefused`，而那是**那一本**的问题。语料库这一屏是人发现问题的入口，
    它自己打不开就把人挡在了外面 ⇒ 跳过那一本，点进去时才拿到那条说得清的错。
    """
    out = []
    for d in paths.iter_novel_dirs(base):
        try:
            out.append(overview(base, paths.derive_source_meta(d.name)[0]))
        except Exception:                        # noqa: BLE001 —— 理由见 docstring
            continue
    return NovelList(base=str(base), novels=out)


@router.get('/{sid}', response_model=NovelOverview)
def get_novel(sid: str = Depends(checked_sid),
              base: str = Depends(get_base)) -> NovelOverview:
    """这本书跑到哪了。**产物存在性 + 来源新鲜度**，不靠进度文件（`R-14.1`）。"""
    return overview(base, sid)
