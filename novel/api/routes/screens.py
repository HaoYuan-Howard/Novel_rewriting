#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screens.py —— 读屏路由（FE-01 点名的那批）。**全部纯读，一个 mutator 都没有。**

【与 `novels.py` 的分界】那边是「语料库 / 总览 / S-06」三个起步端点；
本模块装的是第二阶段补的读屏（S-05 全局 · S-11 文案 · S-12 人名 · 待人工清单，
批 2 再加章级四条）。分文件只为找得到，纪律一致：
  ⚠ 响应模型里一个 `badge` / `passed` / `counts` 都没有（`R-11.4`）——
    结论只能来自 `/api/novels/:sid/check/:kind`。
  ⚠ 「这本书不在」是 404；「这本书在、这一步还没跑」**在这些屏上也是 404**
    （与 S-06 不同：这些屏没有「左半边」，产物缺席就没有内容可看；
    `kind == 'UpstreamMissing'` 让前端把它渲染成「下一步点这里」而不是报错，`U-17`）。
"""
from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from contracts.schema.api import (
    AnalyzeDraftView, BlueprintReadingWorkpack, CharacterRegistryView, GlobalView, InboxView,
    ReadingView, ReviewView,
    Stage4View, Stage5View, Stage6View, Stage7View,
)
from contracts.schema.plot_review import ReviewScope
from contracts.schema.analyze_draft import DraftOwner
from novel.api.deps import checked_sid, get_base
from novel import paths
from novel.views.inbox import inbox_view
from novel.views.stage2 import (
    analyze_draft_view, blueprint_reading_workpack, character_registry_view, global_view, reading_view,
)
from novel.views.stage4 import stage4_view
from novel.views.stage5 import review_view, stage5_view
from novel.views.stage6 import stage6_view
from novel.views.stage7 import stage7_view

router = APIRouter(prefix='/novels')


@router.get('/{sid}/reading/{ordinal}', response_model=ReadingView,
            response_model_by_alias=True)
def get_reading(ordinal: int, sid: str = Depends(checked_sid),
                base: str = Depends(get_base)) -> ReadingView:
    """S-04 原文↔骨架。stage2 没跑 ⇒ 200 且 `blueprint=false`（左半边是 stage1 的）。"""
    return reading_view(base, sid, ordinal)


@router.get('/{sid}/stage4/{ordinal}', response_model=Stage4View,
            response_model_by_alias=True)
def get_stage4(ordinal: int, sid: str = Depends(checked_sid),
               base: str = Depends(get_base)) -> Stage4View:
    """S-07 计划↔上下文。没跑过 stage4 ⇒ 200 恒等基线（`identity_plan=true`）。"""
    return stage4_view(base, sid, ordinal)


@router.get('/{sid}/stage5/{ordinal}', response_model=Stage5View,
            response_model_by_alias=True)
def get_stage5(ordinal: int, sid: str = Depends(checked_sid),
               base: str = Depends(get_base)) -> Stage5View:
    """S-08 成稿↔材料。`ctx_exists=false` ⇒ 不显示假材料；无稿的章 ⇒ 空 drafts。"""
    return stage5_view(base, sid, ordinal)


@router.get('/{sid}/review/{ordinal}', response_model=ReviewView,
            response_model_by_alias=True)
def get_review(ordinal: int, sid: str = Depends(checked_sid),
               base: str = Depends(get_base)) -> ReviewView:
    """S-09 验收/修订（最新一份验收产物）。一份都没有 ⇒ 404。"""
    return review_view(base, sid, ordinal)


@router.get('/{sid}/stage2/characters', response_model=CharacterRegistryView,
            response_model_by_alias=True)
def get_stage2_characters(sid: str = Depends(checked_sid),
                         base: str = Depends(get_base)) -> CharacterRegistryView:
    return character_registry_view(base, sid)


@router.get('/{sid}/stage2/analyze-draft', response_model=AnalyzeDraftView,
            response_model_by_alias=True)
def get_stage2_analyze_draft(owner: DraftOwner, sid: str = Depends(checked_sid),
                            base: str = Depends(get_base)) -> AnalyzeDraftView:
    return analyze_draft_view(base, sid, owner)


@router.get('/{sid}/stage2/global', response_model=GlobalView, response_model_by_alias=True)
def get_stage2_global(sid: str = Depends(checked_sid),
                      base: str = Depends(get_base)) -> GlobalView:
    """S-05 全局设定。剧透遮蔽是前端的纯展示行为（`R-11.13`），本端点全量下发。"""
    return global_view(base, sid)


@router.get(
    '/{sid}/stage2/blueprint-reading-workpack',
    response_model=BlueprintReadingWorkpack,
    response_model_by_alias=True,
)
def get_stage2_blueprint_reading_workpack(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
    scope: ReviewScope | None = None,
) -> BlueprintReadingWorkpack:
    """只读阅读工作包：原文与完整剧情节点各一份；默认跨章全文。

    ⚠ 旧路径 `.../blueprint-review-workpack` 已随 Stage2 语义审阅退役删除
    （2026-09）：审阅语义整体不存在，只读阅读改用本路径，前端同批更新。
    """
    return blueprint_reading_workpack(base, sid, scope=scope)


@router.get('/{sid}/stage6', response_model=Stage6View, response_model_by_alias=True)
def get_stage6(sid: str = Depends(checked_sid),
               base: str = Depends(get_base)) -> Stage6View:
    """S-11 开篇文案。定稿动作在 `POST /{sid}/stage6/approve`（批 3）。"""
    return stage6_view(base, sid)


@router.get('/{sid}/stage7', response_model=Stage7View, response_model_by_alias=True)
def get_stage7(sid: str = Depends(checked_sid),
               base: str = Depends(get_base)) -> Stage7View:
    """S-12 人名替换 + 成书（书级：`ordinal=null, rows=null`）。"""
    return stage7_view(base, sid)


@router.get('/{sid}/stage7/book.txt', response_class=PlainTextResponse)
def get_stage7_book(
    download: bool = Query(default=False),
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> PlainTextResponse:
    """只返回当前已批准、输入新鲜且组装账闭合的最终交付物。

    ⚠ **URL 与盘上文件名是两件事。** 这条路由的字面（`.../stage7/book.txt`）是 API
    标识符，`R-11.x` 那张路由表钉着它；盘上那份叫 `<书名>_rewriting.txt`。
    改 URL 去追文件名会破坏冻结的 API 面，而**人要的是另存下来的名字**——那由
    `Content-Disposition` 决定，落点就是下面这一处。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    from novel.orchestrator.delivery import read_deliverable_book

    text = read_deliverable_book(base, sid)
    headers = _download_headers(novel_dir) if download else None
    return PlainTextResponse(
        text, media_type='text/plain; charset=utf-8',
        headers=headers)


def _download_headers(novel_dir) -> dict[str, str]:
    """另存文件名。**必须同时给 `filename` 与 `filename*`**（RFC 6266 / RFC 5987）。

    ⚠⚠ 书名是中文，而 `Content-Disposition` 的 header 值只能是 latin-1：
    直接把中文塞进 `filename="…"` 会在 ASGI 编码 header 时抛
    `UnicodeEncodeError`（500），或者被中间层换成一串问号。
    ⇒ 中文走 `filename*=UTF-8''<百分号编码>`；`filename=` 留一个纯 ASCII 回退，
    给不认 `filename*` 的老客户端。两者都给时现代浏览器取 `filename*`。

    ⚠ ASCII 回退**刻意不是书名的音译或删字版**：那会产出一个看起来像书名、
    其实是另一个名字的文件。回退就老实叫 `book.txt`——它与 URL 同名，说得清来历。
    """
    name = os.path.basename(paths.book_txt_rel(novel_dir))
    quoted = quote(name, safe='')
    return {
        'Content-Disposition':
            f'attachment; filename="book.txt"; filename*=UTF-8\'\'{quoted}',
    }


@router.get('/{sid}/stage7/{ordinal}', response_model=Stage7View,
            response_model_by_alias=True)
def get_stage7_chapter(ordinal: int, sid: str = Depends(checked_sid),
                       base: str = Depends(get_base)) -> Stage7View:
    """S-12 的逐章对照。`:o` 不在判过的章集里 ⇒ 404（资源不存在）。"""
    return stage7_view(base, sid, ordinal)


@router.get('/{sid}/inbox', response_model=InboxView)
def get_inbox(sid: str = Depends(checked_sid),
              base: str = Depends(get_base)) -> InboxView:
    """待人工八类清单（`docs/BACKEND_CONTRACT.md` §七的 `human` 落面）。

    ⚠ 它对每个已移植层各跑一次 0 成本复检（stage2/4 会重算判据）——**纯读**，
    大书上是秒级。`inbox_view` 的 docstring 说缓存归路由层：**登记不优化**，
    盘上 3 本无压力；全语料 163 本嫌慢时再在这里加缓存，别动生产者。
    """
    return inbox_view(base, sid)
