#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目级维护端点。产物目录移动只发生在 ``novel.cli.trash`` 子进程。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import TrashNovelRequest, TrashNovelResult
from novel.api.deps import checked_sid, get_base
from novel.orchestrator import trash

router = APIRouter(prefix='/novels')


@router.post('/{sid}/trash', response_model=TrashNovelResult)
def trash_novel(
    request: TrashNovelRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> TrashNovelResult:
    """把整本项目移入可恢复回收站；书名确认与 sid 双重绑定当前目标。"""
    return trash.run(base, sid, request.expected_title)
