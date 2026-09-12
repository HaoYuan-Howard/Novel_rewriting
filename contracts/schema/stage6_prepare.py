"""Stage6 独立文案准备：模型提语义与段引用，代码编译证据并记录独立接受。"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from contracts.invariants import STAGE6_PREPARE_DECISIONS

from contracts.schema.base import StrictModel, Sha1
from contracts.schema.common import HumanApproval, LlmProvenance, Sha256
from contracts.schema.stage1 import MainText, HookCopy
from contracts.schema.stage2 import SourceBlurbGrounding, FactEvidence, HookFactEvidence


class PrepareParagraphRef(StrictModel):
    start_para: int = Field(ge=1, strict=True)
    end_para: int = Field(ge=1, strict=True)

    @model_validator(mode='after')
    def _range(self) -> Self:
        if self.end_para < self.start_para:
            raise ValueError('证据段范围必须连续且正序')
        return self


class PrepareMainEvidence(PrepareParagraphRef):
    chapter_ordinal: int = Field(ge=1, strict=True)


class PrepareDraft(SourceBlurbGrounding[PrepareMainEvidence, PrepareParagraphRef]):
    """仅事实支持与从首章起的连续范围，不携重复全文或模型计算的字符坐标。"""
    facts: SourceBlurbGrounding[PrepareMainEvidence, PrepareParagraphRef].model_fields['facts'].annotation = Field(min_length=0)


class PrepareResponse(StrictModel):
    source_blurb: PrepareDraft


class PrepareEdit(HumanApproval):
    review_token: Annotated[str, Field(pattern=r'^s6p1_[0-9a-f]{64}$')]
    source_sha256: Sha256
    # 材料内容的 canonical SHA；不是源文件排版或审计字节的全对象业务边。
    main_text_sha256: Sha256
    hook_copy_sha256: Sha256
    source_blurb: PrepareDraft


class PrepareEditReceipt(HumanApproval):
    request_sha256: Sha256
    source_sha256: Sha256
    at: str = Field(min_length=1)


class PrepareSourceRebuild(StrictModel):
    """免费完整重编采用当前源；模型和费用仍属于可回读的原候选历史。"""
    source_sha256: Sha256
    source_root_clean_sha1: Sha1
    target_root_clean_sha1: Sha1
    generation_root_clean_sha1: Sha1
    source_revision_id: Sha256
    target_revision_id: Sha256
    main_text_sha256: Sha256
    hook_copy_sha256: Sha256


class PrepareProvenance(LlmProvenance):
    prepare_edit: PrepareEditReceipt | None = None
    source_rebuild: PrepareSourceRebuild | None = None


class Stage6Prepare(StrictModel):
    source_blurb: SourceBlurbGrounding[FactEvidence, HookFactEvidence]
    main_text_sha256: Sha256
    hook_copy_sha256: Sha256
    provenance: PrepareProvenance = Field(alias='_provenance')


class PrepareDecision(HumanApproval):
    review_token: Annotated[str, Field(pattern=r'^s6p1_[0-9a-f]{64}$')]
    source_sha256: Sha256
    decision: Literal[STAGE6_PREPARE_DECISIONS]
    edit: PrepareEdit | None = None

    @model_validator(mode='after')
    def _same_owner(self) -> Self:
        if self.edit is not None and (self.edit.source_sha256 != self.source_sha256
                or self.edit.review_token != self.review_token
                or self.edit.actor != self.actor or self.edit.reason != self.reason):
            raise ValueError('同批 Prepare 编辑必须绑定相同主体及 actor/reason')
        return self


class Stage6PrepareReview(HumanApproval):
    source_sha256: Sha256
    request_sha256: Sha256
    decision: Literal[STAGE6_PREPARE_DECISIONS]
    at: str = Field(min_length=1)


class PreparePaidSource(StrictModel):
    kind: Literal['paid'] = 'paid'
    source_run_id: str = Field(min_length=1)
    checkpoint_sha256: Sha256
    input_sha256: Sha256


class PrepareMutationJournal(StrictModel):
    """已购发布与免费编辑/决定的 write-ahead 记录；完成后历史来源仍不可变。"""
    state: Literal['prepared', 'completed']
    run_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    request_sha256: Sha256
    request: PrepareDecision | PrepareEdit | PreparePaidSource
    baselines: dict[str, Sha256 | None]
    candidate: Stage6Prepare
    candidate_sha256: Sha256
    review: Stage6PrepareReview | None
    review_sha256: Sha256 | None
    at: str = Field(min_length=1)
    hard_issues: list[str] = Field(default_factory=list)


class PrepareState(StrictModel):
    state: Literal['missing', 'invalid', 'pending', 'accepted', 'stale', 'unknown']
    reason: str
    subject_sha256: Sha256 | None
    review_token: str | None
    prepare: Stage6Prepare | None
    review: Stage6PrepareReview | None
    pending_commit: bool


class PrepareWorkpack(StrictModel):
    state: PrepareState
    # 全文与源钩子各只下发一次，候选段号由同一 paragraph_spans 生成。
    main_text: MainText
    hook_copy: HookCopy
    edit_template: PrepareEdit
    decision_template: PrepareDecision
    review_contract: str
