#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""str06.py —— STR-06 候选链的只读监控路由（F7d）。**全部 GET，一个 mutator 都没有。**

【为什么单独一个前缀】候选链在 F8 激活前不进现役注册表：这些端点挂在
`/api/str06/candidates/...` 下，与现役 `/api/novels/:sid/...` 物理分开——
监控页因此也没有任何入口伪装成「这本书已经切到新链」。切换归 F8b，届时本文件
随候选身份一并激活或分流，不为切换保留第二套路由。

【纪律与现役读屏一致】
  · 响应模型里没有 `badge` / `passed` / `counts`（`R-11.4`）：候选链的判定只有
    现役 check 报告与机械回执的 `mechanical_checks`，本路由只转盘面事实；
  · 服务进程不落产物盘（`R-11.3`）：全部经 `novel/views/str06.py` 的只读投影，
    下载也只是把已落盘的成书字节流出去；
  · 「没有这个候选根」是 404；「候选根在、这一腿还没跑」是 200 且 `present=false`
    ——监控页要能显示「跑到一半」，而不是把半程当错误。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from contracts.schema.api import (
    StoryArtifactView, Str06CandidateList, Str06DeliveryView, Str06RunView,
)

from novel.api.deps import get_base
from novel.errors import UpstreamMissing
from novel.views import str06 as views

router = APIRouter(prefix='/str06/candidates')


def _require_candidate(base: str, candidate: str) -> None:
    """候选根不存在（或名字不合法）⇒ 404；半程候选根本身就在。"""
    if not views.run_view(base, candidate).present:
        raise UpstreamMissing(
            f'没有这个候选根：{candidate}',
            hint='候选根是输出根下带 runtime/candidate_workflow.json 的目录；'
                 '用 GET /api/str06/candidates 看现有清单')


@router.get('', response_model=Str06CandidateList)
def list_candidates(base: str = Depends(get_base)) -> Str06CandidateList:
    """输出根下的全部候选根与各自 run 级状态。"""
    return views.list_candidates(base)


@router.get('/{candidate}/run', response_model=Str06RunView)
def get_run(candidate: str, base: str = Depends(get_base)) -> Str06RunView:
    """候选根总览：workflow 状态、epoch、节点盘面事实与 ledger 成本账。"""
    _require_candidate(base, candidate)
    return views.run_view(base, candidate)


@router.get('/{candidate}/source-story', response_model=StoryArtifactView)
def get_source_story(candidate: str, base: str = Depends(get_base)) -> StoryArtifactView:
    _require_candidate(base, candidate)
    return views.source_story_view(base, candidate)


@router.get('/{candidate}/target-story', response_model=StoryArtifactView)
def get_target_story(candidate: str, base: str = Depends(get_base)) -> StoryArtifactView:
    _require_candidate(base, candidate)
    return views.target_story_view(base, candidate)


@router.get('/{candidate}/chapters/{ordinal}', response_model=StoryArtifactView)
def get_chapter(ordinal: int, candidate: str, base: str = Depends(get_base)) -> StoryArtifactView:
    _require_candidate(base, candidate)
    if ordinal < 1:
        raise HTTPException(status_code=400, detail='章序必须从 1 开始')
    return views.chapter_view(base, candidate, ordinal)


@router.get('/{candidate}/hook', response_model=StoryArtifactView)
def get_hook(candidate: str, base: str = Depends(get_base)) -> StoryArtifactView:
    _require_candidate(base, candidate)
    return views.hook_view(base, candidate)


@router.get('/{candidate}/delivery', response_model=Str06DeliveryView)
def get_delivery(candidate: str, base: str = Depends(get_base)) -> Str06DeliveryView:
    """成书交付：manifest 的就绪核对记录与全书篇幅读数。"""
    _require_candidate(base, candidate)
    return views.delivery_view(base, candidate)


def _download_headers(name: str) -> dict[str, str]:
    """另存文件名。**必须同时给 `filename` 与 `filename*`**（RFC 6266 / RFC 5987）。

    ⚠⚠ 书名是中文，而 `Content-Disposition` 的 header 值只能是 latin-1：直接把
    中文塞进 `filename="…"` 会在 ASGI 编码 header 时抛 `UnicodeEncodeError`
    （500），或被中间层换成一串问号 ⇒ 中文走 `filename*=UTF-8''<百分号编码>`，
    `filename=` 留一个纯 ASCII 回退（`book.txt`，与 URL 同名，说得清来历）。
    """
    quoted = quote(name, safe='')
    return {
        'Content-Disposition':
            "attachment; filename=\"book.txt\"; filename*=UTF-8''" + quoted,
    }


@router.get('/{candidate}/book')
def get_book(candidate: str, download: bool = Query(default=False),
             base: str = Depends(get_base)) -> PlainTextResponse:
    """成书字节下载（`stage7/final/{title}_rewriting.txt` 原样流出）。

    ⚠ 没有成书是 404，不是空响应：文件存在不等于可交付，而空 200 会让页面
    把「还没成书」显示成「一本空书」。
    ⚠ `download=true` 时带 `Content-Disposition`（承接旧 `stage7/book.txt`
    的另存名语义，F8b）：另存文件名用**盘上那份的名字**，不让前端拿书名重拼——
    同一条命名规则不该有两个落点（`R-11.4`）。
    """
    _require_candidate(base, candidate)
    delivery = views.final_text_delivery(base, candidate)
    if delivery is None:
        raise UpstreamMissing(
            f'候选根 {candidate} 还没有成书文件',
            hint='跑到 stage7 assemble 且 manifest 提交后才有成书；'
                 '用 /delivery 看就绪状态')
    filename, verified_text = delivery
    headers = _download_headers(filename) if download else None
    return PlainTextResponse(verified_text, headers=headers)
