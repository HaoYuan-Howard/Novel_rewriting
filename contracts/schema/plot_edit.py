"""Blueprint 的章级订正与身份账；身份声明不替代语义审阅。"""
from __future__ import annotations

from typing import Literal, Self
from pydantic import Field, model_validator
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.plot import NonEmpty, PlotChapterResponse, PlotNodeTombstone, unique


class PlotIdentityOperation(StrictModel):
    collection: Literal['beats', 'states', 'constraints']
    kind: Literal['preserve', 'replace', 'add']
    old_refs: list[NonEmpty]
    new_keys: list[NonEmpty]

    @model_validator(mode='after')
    def _shape(self) -> Self:
        unique(self.old_refs, '旧主体')
        unique(self.new_keys, '新主体')
        if self.kind == 'preserve' and (len(self.old_refs), len(self.new_keys)) != (1, 1):
            raise ValueError('保留身份只能一对一')
        if self.kind == 'add' and (self.old_refs or not self.new_keys):
            raise ValueError('新增主体不能有旧引用，必须有输出')
        if self.kind == 'replace' and not self.old_refs:
            raise ValueError('拆合或删除必须指明旧主体')
        return self


class PlotChapterEdit(StrictModel):
    chapter: PlotChapterResponse
    identities: list[PlotIdentityOperation]


class PlotChapterRevisionResponse(PlotChapterResponse):
    """定向模型回答沿用章响应，加显式身份操作；不让模型分配正式 ID。"""
    identities: list[PlotIdentityOperation]


class PlotRevisionResponse(StrictModel):
    blueprints: list[PlotChapterRevisionResponse] = Field(min_length=1)


class PlotEdit(StrictModel):
    actor: NonEmpty
    reason: NonEmpty
    review_token: str = Field(pattern=r'^bpe1_[0-9a-f]{64}$')
    chapters: list[PlotChapterEdit] = Field(min_length=1)

    @model_validator(mode='after')
    def _order(self) -> Self:
        ordinals = [row.chapter.chapter_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('编辑章号必须升序且不重复')
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('编辑人和理由不能为空白')
        return self


class PlotIdentityChange(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    collection: Literal['beats', 'states', 'constraints']
    kind: Literal['preserve', 'replace', 'add']
    old_refs: list[NonEmpty]
    new_refs: list[NonEmpty]

    @model_validator(mode='after')
    def _shape(self) -> Self:
        import re
        PlotIdentityOperation(collection=self.collection, kind=self.kind,
                              old_refs=self.old_refs, new_keys=self.new_refs)
        prefix = {'beats': 'b', 'states': 'st', 'constraints': 'c'}[self.collection]
        if any(not re.fullmatch(rf'{prefix}{self.chapter_ordinal:02d}_\d{{2,}}', ref)
               for ref in [*self.old_refs, *self.new_refs]):
            raise ValueError('身份历史中的主体不属于该章或该集合')
        if self.kind == 'preserve' and self.old_refs != self.new_refs:
            raise ValueError('保留身份不能更换正式 ID')
        if self.kind == 'replace' and set(self.old_refs) & set(self.new_refs):
            raise ValueError('拆合后的主体必须使用新 ID')
        return self


class PlotRevisionRecord(StrictModel):
    request_sha256: Sha256
    before_subject_sha256: Sha256
    result_subject_sha256: Sha256
    actor: NonEmpty
    reason: NonEmpty
    changes: list[PlotIdentityChange]
    tombstones: list[PlotNodeTombstone]

    @model_validator(mode='after')
    def _identity_history(self) -> Self:
        old_refs = [ref for row in self.changes for ref in row.old_refs]
        new_refs = [ref for row in self.changes for ref in row.new_refs]
        unique(old_refs, '同次修订的旧主体')
        unique(new_refs, '同次修订的新主体')
        unique([row.node_id for row in self.tombstones], '退役主体')
        expected = {ref: row.new_refs for row in self.changes if row.kind == 'replace'
                    for ref in row.old_refs}
        actual = {row.node_id: row.superseded_by for row in self.tombstones}
        if expected != actual:
            raise ValueError('替代映射与退役记录不一致')
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('身份历史必须包含编辑人和理由')
        return self


class PlotEditPreview(StrictModel):
    review_token: str
    result_subject_sha256: Sha256
    changed_chapters: list[int]
    changes: list[PlotIdentityChange]
    tombstones: list[PlotNodeTombstone]
    hard_hits: list[str]
    would_apply: bool
