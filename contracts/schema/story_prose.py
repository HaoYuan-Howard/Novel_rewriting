"""story-v1 写作与交付契约：正文、真实来源回执和受限开篇补丁。

模型只提供正文或明确的设计缺口；ID、请求身份和字节摘要由运行代码生成。
机械回执不证明文学质量。来源字段由耐久回答边界填入，不由模型自报。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.target_story import ChapterPacket, HookPacket

Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Section = Literal['must_realize', 'must_respect', 'continuity', 'creative_guidance',
                  'background', 'guard_only', 'entry_state', 'exit_state', 'story_structure']
PARAGRAPH_BREAKS = '\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029'


class ProseDraft(FrozenModel):
    kind: Literal['prose']
    text: str = Field(min_length=1)


class DesignProblem(FrozenModel):
    section: Section
    item_no: int | None = Field(strict=True, ge=1)
    detail: Nonempty

    @model_validator(mode='after')
    def explicit_location(self):
        if (self.section == 'story_structure') != (self.item_no is None):
            raise ValueError('局部问题必须引用已给材料序号；无法定位的story_structure须显式item_no=null')
        return self


class NeedsDesign(FrozenModel):
    kind: Literal['needs_design']
    problems: tuple[DesignProblem, ...] = Field(min_length=1)


ProseAnswer = Annotated[ProseDraft | NeedsDesign, Field(discriminator='kind')]


class BodyRequest(FrozenModel):
    contract_version: Literal['story-v1']
    kind: Literal['body'] = 'body'
    packet: ChapterPacket
    packet_sha256: Digest
    prior_tail: str
    prior_tail_sha256: Digest
    prior_tail_status: Literal['first_chapter', 'available', 'no_complete_paragraph_fits']
    system: str
    user: str
    request_id: Digest


class HookRequest(FrozenModel):
    contract_version: Literal['story-v1']
    kind: Literal['hook'] = 'hook'
    packet: HookPacket
    packet_sha256: Digest
    system: str
    user: str
    request_id: Digest


class ProseIssue(FrozenModel):
    code: Nonempty
    owner: Literal['stage4', 'stage5', 'stage7']
    chapter_ordinal: int | None
    detail: Nonempty


class OpeningParagraph(FrozenModel):
    handle: Nonempty
    ordinal: int = Field(strict=True, ge=1, le=8)
    text: str = Field(min_length=1)


class OpeningPatchRequest(FrozenModel):
    contract_version: Literal['story-v1']
    kind: Literal['opening_patch'] = 'opening_patch'
    chapter_ordinal: int = Field(strict=True, ge=1)
    packet_sha256: Digest
    prior_tail_sha256: Digest
    base_body_sha256: Digest
    base_receipt_sha256: Digest
    paragraphs: tuple[OpeningParagraph, ...] = Field(min_length=1, max_length=8)
    system: str
    user: str
    request_id: Digest


class KeepParagraph(FrozenModel):
    kind: Literal['keep']
    handle: Nonempty


class ReplaceParagraph(FrozenModel):
    kind: Literal['replace']
    handle: Nonempty
    text: str = Field(min_length=1)

    @model_validator(mode='after')
    def single_paragraph(self):
        if not self.text.strip() or any(char in self.text for char in PARAGRAPH_BREAKS):
            raise ValueError('replace 必须是单个非空自然段，不得增加换行')
        return self


ParagraphOperation = Annotated[KeepParagraph | ReplaceParagraph, Field(discriminator='kind')]


class OpeningPatch(FrozenModel):
    kind: Literal['patch']
    paragraphs: tuple[ParagraphOperation, ...] = Field(min_length=1, max_length=8)


class NeedsScope(FrozenModel):
    kind: Literal['needs_scope']
    detail: Nonempty


OpeningPatchAnswer = Annotated[OpeningPatch | NeedsScope, Field(discriminator='kind')]


class PatchApplication(FrozenModel):
    request_id: Digest
    response_id: Nonempty
    provider: Nonempty
    model: Nonempty
    packet_sha256: Digest
    prior_tail_sha256: Digest
    system_sha256: Digest
    user_sha256: Digest
    base_body_sha256: Digest
    result_body_sha256: Digest
    base_receipt_sha256: Digest
    answer_sha256: Digest
    changed_handles: tuple[str, ...]

    @model_validator(mode='after')
    def bounded_handles(self):
        if len(self.changed_handles) > 8 or len(set(self.changed_handles)) != len(self.changed_handles):
            raise ValueError('开篇补丁不得重复或超过八个自然段句柄')
        return self


class ProseReceipt(FrozenModel):
    contract_version: Literal['story-v1']
    kind: Literal['body', 'hook']
    chapter_ordinal: int | None = Field(strict=True, ge=1)
    packet_sha256: Digest
    prior_tail_sha256: Digest
    original_prior_tail_sha256: Digest
    body_sha256: Digest
    original_body_sha256: Digest
    request_id: Digest
    response_id: Nonempty
    provider: Nonempty
    model: Nonempty
    system_sha256: Digest
    user_sha256: Digest
    finish_reason: Literal['stop']
    mechanical: Literal['passed']
    semantic: Literal['unver']
    patches: tuple[PatchApplication, ...]

    @model_validator(mode='after')
    def explicit_kind(self):
        if (self.kind == 'body') != (self.chapter_ordinal is not None):
            raise ValueError('body 必须绑定章序，hook 章序必须为 null')
        if self.chapter_ordinal is not None and self.chapter_ordinal < 1:
            raise ValueError('章序必须为正整数')
        if self.kind == 'hook' and self.patches:
            raise ValueError('Hook 不接受正文开篇补丁')
        return self


class ChapterBody(FrozenModel):
    contract_version: Literal['story-v1']
    chapter_ordinal: int = Field(strict=True, ge=1)
    text: str = Field(min_length=1)
    receipt: ProseReceipt

    @model_validator(mode='after')
    def chapter_matches(self):
        if self.receipt.kind != 'body' or self.receipt.chapter_ordinal != self.chapter_ordinal:
            raise ValueError('正文与回执章序不一致')
        return self


class HookBody(FrozenModel):
    contract_version: Literal['story-v1']
    text: str = Field(min_length=1)
    receipt: ProseReceipt

    @model_validator(mode='after')
    def hook_matches(self):
        if self.receipt.kind != 'hook':
            raise ValueError('Hook 必须使用 Hook 回执')
        return self


class FinalChapterEntry(FrozenModel):
    chapter_ordinal: int = Field(strict=True, ge=1)
    packet_sha256: Digest
    prior_tail_sha256: Digest
    body_sha256: Digest
    receipt_sha256: Digest
    request_id: Digest


class FinalManifest(FrozenModel):
    contract_version: Literal['story-v1']
    title: Nonempty
    source_chapter_count: int = Field(strict=True, ge=1)
    chapters: tuple[FinalChapterEntry, ...]
    hook_packet_sha256: Digest
    hook_body_sha256: Digest
    hook_receipt_sha256: Digest
    final_text_sha256: Digest
    mechanical: Literal['passed']
    semantic: Literal['unver']

    @model_validator(mode='after')
    def exact_chapters(self):
        if tuple(row.chapter_ordinal for row in self.chapters) != tuple(range(1, self.source_chapter_count + 1)):
            raise ValueError('最终目录必须完整保留源章数及阅读序号')
        return self


class FinalStory(FrozenModel):
    contract_version: Literal['story-v1']
    text: str = Field(min_length=1)
    manifest: FinalManifest
