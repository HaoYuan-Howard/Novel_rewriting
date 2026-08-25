#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage1 分章产物契约。

本层只持久化下游真正消费的两份材料：逐章正文与开篇文案。原文章号、切片区间、
章节计数和告警都由当前 ``stage0/clean_text.txt`` 复扫得到，不另存一份会漂移的副本。
分章是确定性的；前端只读，切错时修改算法并从 Stage1 重建。
"""
from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field, RootModel, model_validator

from contracts.invariants import MIN_CHAPTERS
from contracts.schema.base import StrictModel


def _non_blank(value: str) -> str:
    """正文类字段必须承载非空白内容。"""
    if not value.strip():
        raise ValueError('正文类字段去空白后为空')
    return value


NonBlankText = Annotated[str, AfterValidator(_non_blank)]


class MainTextSection(StrictModel):
    """``main_text.json`` 的一章；全链唯一章节键是阅读序。"""

    chapter_ordinal: int = Field(ge=1)
    text: NonBlankText


class MainText(RootModel[Annotated[list[MainTextSection], Field(min_length=MIN_CHAPTERS)]]):
    """``stage1/main_text.json``；顶层是至少两章的数组。"""

    @model_validator(mode='after')
    def _ordinals_are_dense_from_one(self) -> MainText:
        got = [section.chapter_ordinal for section in self.root]
        want = list(range(1, len(got) + 1))
        if got != want:
            raise ValueError(f'chapter_ordinal 不是从 1 起的连续序列：{got[:20]}…')
        return self


class HookCopy(StrictModel):
    """``stage1/hook_copy.json``；第一个正文章标记之前的非空文案。"""

    text: NonBlankText
