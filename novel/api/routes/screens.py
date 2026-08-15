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

from fastapi import APIRouter, Depends

from contracts.schema.api import (
    GlobalView, InboxView, ReadingView, ReviewView,
    Stage4View, Stage5View, Stage6View, Stage7View,
)
from novel.api.deps import checked_sid, get_base
from novel.views.inbox import inbox_view
from novel.views.stage2 import global_view, reading_view
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


@router.get('/{sid}/stage2/global', response_model=GlobalView, response_model_by_alias=True)
def get_stage2_global(sid: str = Depends(checked_sid),
                      base: str = Depends(get_base)) -> GlobalView:
    """S-05 全局设定。剧透遮蔽是前端的纯展示行为（`R-11.13`），本端点全量下发。"""
    return global_view(base, sid)


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


@router.get('/{sid}/stage7/{ordinal}', response_model=Stage7View,
            response_model_by_alias=True)
def get_stage7_chapter(ordinal: int, sid: str = Depends(checked_sid),
                       base: str = Depends(get_base)) -> Stage7View:
    """S-12 的逐章对照。`:o` 不在判过的章集里 ⇒ 404（资源不存在）。"""
    return stage7_view(base, sid, ordinal)


@router.get('/{sid}/inbox', response_model=InboxView)
def get_inbox(sid: str = Depends(checked_sid),
              base: str = Depends(get_base)) -> InboxView:
    """待人工六类清单（`docs/BACKEND_CONTRACT.md` §七的 `human` 落面）。

    ⚠ 它对每个已移植层各跑一次 0 成本复检（stage2/4 会重算判据）——**纯读**，
    大书上是秒级。`inbox_view` 的 docstring 说缓存归路由层：**登记不优化**，
    盘上 3 本无压力；全语料 163 本嫌慢时再在这里加缓存，别动生产者。
    """
    return inbox_view(base, sid)
