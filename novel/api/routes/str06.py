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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from contracts.schema.str06.api import (
    Str06BlueprintView, Str06CandidateList, Str06CharactersView, Str06ChapterView,
    Str06DeliveryView, Str06DesignView, Str06HookView, Str06IrNavView,
    Str06NamingView, Str06RunView,
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


@router.get('/{candidate}/characters', response_model=Str06CharactersView)
def get_characters(candidate: str, base: str = Depends(get_base)) -> Str06CharactersView:
    """Characters v4：人物条目（称谓时间窗）与源名称结构。"""
    _require_candidate(base, candidate)
    return views.characters_view(base, candidate)


@router.get('/{candidate}/blueprint', response_model=Str06BlueprintView)
def get_blueprint(candidate: str, base: str = Depends(get_base)) -> Str06BlueprintView:
    """Blueprint v12：世界约束、逐章 beats 与因果边。"""
    _require_candidate(base, candidate)
    return views.blueprint_view(base, candidate)


@router.get('/{candidate}/design', response_model=Str06DesignView)
def get_design(candidate: str, base: str = Depends(get_base)) -> Str06DesignView:
    """graph／binding：场景驱动与导演说明、章目的、Hook 选择与 Binding 条目。"""
    _require_candidate(base, candidate)
    return views.design_view(base, candidate)


@router.get('/{candidate}/ir', response_model=Str06IrNavView)
def get_ir(candidate: str, base: str = Depends(get_base)) -> Str06IrNavView:
    """WriterBookIR 导航：章预算、章目的、逐场摘要行与称谓释放位置表。"""
    _require_candidate(base, candidate)
    return views.ir_nav_view(base, candidate)


@router.get('/{candidate}/chapters/{ordinal}', response_model=Str06ChapterView)
def get_chapter(ordinal: int, candidate: str,
                base: str = Depends(get_base)) -> Str06ChapterView:
    """一章的正文与章级回执（机械范围、语义未评、篇幅披露）。"""
    _require_candidate(base, candidate)
    if ordinal < 1:
        raise HTTPException(status_code=400, detail='章序必须从 1 开始')
    return views.chapter_view(base, candidate, ordinal)


@router.get('/{candidate}/hook', response_model=Str06HookView)
def get_hook(candidate: str, base: str = Depends(get_base)) -> Str06HookView:
    """Hook 成文与机械回执。"""
    _require_candidate(base, candidate)
    return views.hook_view(base, candidate)


@router.get('/{candidate}/naming', response_model=Str06NamingView)
def get_naming(candidate: str, base: str = Depends(get_base)) -> Str06NamingView:
    """NameMap v5：交付变换契约的只读投影。"""
    _require_candidate(base, candidate)
    return views.naming_view(base, candidate)


@router.get('/{candidate}/delivery', response_model=Str06DeliveryView)
def get_delivery(candidate: str, base: str = Depends(get_base)) -> Str06DeliveryView:
    """成书交付：manifest 的就绪核对记录与全书篇幅读数。"""
    _require_candidate(base, candidate)
    return views.delivery_view(base, candidate)


@router.get('/{candidate}/book')
def get_book(candidate: str, base: str = Depends(get_base)) -> PlainTextResponse:
    """成书字节下载（`stage7/final/{title}_rewriting.txt` 原样流出）。

    ⚠ 没有成书是 404，不是空响应：文件存在不等于可交付，而空 200 会让页面
    把「还没成书」显示成「一本空书」。
    """
    _require_candidate(base, candidate)
    path = views.final_text_path(base, candidate)
    if path is None:
        raise UpstreamMissing(
            f'候选根 {candidate} 还没有成书文件',
            hint='跑到 stage7 assemble 且 manifest 提交后才有成书；'
                 '用 /delivery 看就绪状态')
    return PlainTextResponse(path.read_text(encoding='utf-8'))
