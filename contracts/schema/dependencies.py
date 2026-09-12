"""实际消费投影的范围身份；主体摘要与验证 seal、执行 epoch 分开。

这里只记录依赖与摘要，不保存第二份模型材料或产生接受。运行时必须用登记的同一个
投影函数构建工作包和复核 freshness；仅 ID 相等不能证明内容未变。
"""
from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256


class ScopeSelection(FrozenModel):
    input_rel: str = Field(min_length=1)
    projection: Literal['plot_chapter', 'source_chapter', 'collection_members',
                        'plot_global', 'plot_inherited_states', 'source_ranges', 'plot_global_review']
    # collection_members 用明确路径和主键；其他两种按阅读序取唯一章。
    chapter_ordinal: int | None = Field(default=None, ge=1, strict=True)
    path: tuple[str, ...] = ()
    key_field: str | None = None
    selected_ids: tuple[str, ...] | None = None

    @model_validator(mode='after')
    def _shape(self) -> Self:
        if self.projection == 'source_ranges':
            import re
            if (self.chapter_ordinal is None or self.path or self.key_field is not None
                    or not self.selected_ids or len(set(self.selected_ids)) != len(self.selected_ids)
                    or any(not re.fullmatch(r'p:[1-9]\d*:[1-9]\d*', ref) for ref in self.selected_ids)):
                raise ValueError('原文证据投影需要唯一章号及不重复的明确段范围')
        elif self.projection == 'plot_global_review':
            if (self.chapter_ordinal is None or self.path or self.key_field is not None
                    or self.selected_ids is None or len(set(self.selected_ids)) != len(self.selected_ids)
                    or any(':' not in ref for ref in self.selected_ids)):
                raise ValueError('审阅共享主体需要明确、不重复的 kind:ref 闭集')
        elif self.projection == 'collection_members':
            if not self.path or not self.key_field or self.chapter_ordinal is not None:
                raise ValueError('集合投影需要明确路径、主键，不接受隐式章过滤')
            if self.selected_ids is not None and (
                    len(set(self.selected_ids)) != len(self.selected_ids)
                    or not all(self.selected_ids)):
                raise ValueError('消费集合成员须非空标识且不重复')
        elif (self.chapter_ordinal is None or self.path or self.key_field is not None
              or self.selected_ids is not None):
            raise ValueError('章节投影只接受明确的 chapter_ordinal')
        return self


class ScopeDependency(FrozenModel):
    selection: ScopeSelection
    consumed_sha256: Sha256


class DependencyManifest(FrozenModel):
    contract: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    inputs: tuple[ScopeDependency, ...]
    depends_on_scopes: tuple[str, ...] = ()

    @model_validator(mode='after')
    def _unique(self) -> Self:
        selectors = [row.selection.model_dump_json() for row in self.inputs]
        if len(selectors) != len(set(selectors)):
            raise ValueError('同一消费投影不能重复登记')
        if (len(self.depends_on_scopes) != len(set(self.depends_on_scopes))
                or self.scope in self.depends_on_scopes):
            raise ValueError('范围依赖不能重复或自指')
        return self
