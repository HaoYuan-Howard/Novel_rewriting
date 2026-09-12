"""章级 WriterVisibleView：剧情只存一份，场景和知识许可均通过引用使用。"""
from __future__ import annotations

from typing import Literal, Self
from pydantic import Field, JsonValue, model_validator
from contracts.schema.base import StrictModel
from contracts.schema.plot import GlobalSubjectRef, PlotSubjectRef
from contracts.schema.knowledge_links import KnowledgeLinkKind
from contracts.schema.stage2 import KnowledgeSubject
from contracts.invariants import STRUCTURAL_ROLES, DELIVERY_MODES, TEMPORAL_LINKS, TIME_LAYERS


class WriterBeat(StrictModel):
    beat_ref: str
    content: str = Field(min_length=1)
    participant_char_ids: list[str]


class WriterState(StrictModel):
    state_ref: str
    content: str | None
    global_ref: GlobalSubjectRef | None
    modality: Literal['world', 'belief', 'claim', 'unresolved']
    holder_char_id: str | None

    @model_validator(mode='after')
    def _proposition(self) -> Self:
        if (self.content is None) == (self.global_ref is None):
            raise ValueError('状态只保存正文或主体引用之一')
        if self.modality in ('belief', 'claim') and self.holder_char_id is None:
            raise ValueError('相信或声称必须有持有者')
        if self.modality == 'world' and self.holder_char_id is not None:
            raise ValueError('客观状态不能有信念持有者')
        return self


class WriterConstraint(StrictModel):
    constraint_ref: str
    kind: Literal['cause', 'story_before', 'response_to', 'same_scene', 'observes',
                  'literal', 'value', 'object']
    before_beat_ref: str | None = None
    after_beat_ref: str | None = None
    observer_char_id: str | None = None
    target_beat_ref: str | None = None
    value: str | None = None

    @model_validator(mode='after')
    def _endpoints(self) -> Self:
        if self.kind in ('literal', 'value', 'object'):
            if not self.target_beat_ref or not self.value or any(
                    (self.before_beat_ref, self.after_beat_ref, self.observer_char_id)):
                raise ValueError('字面类约束只带目标节点与必要参数')
        elif (not self.before_beat_ref or not self.after_beat_ref
              or self.before_beat_ref == self.after_beat_ref
              or self.target_beat_ref is not None or self.value is not None
              or (self.kind == 'observes') != (self.observer_char_id is not None)):
            raise ValueError('关系约束须有两个不同端点，仅观察约束指定观察者')
        return self


class WriterStatePermission(StrictModel):
    state_ref: str
    modality: Literal['belief', 'claim', 'unresolved']
    allowed_slots: list[str] = Field(min_length=1)


class WriterGlobalSubject(StrictModel):
    subject_ref: GlobalSubjectRef
    value: JsonValue
    # factual allowed_slots 为空时，仅可在 state_permissions 内以信念/声称/未知表达。
    allowed_slots: list[str]
    state_permissions: list[WriterStatePermission]

    @model_validator(mode='after')
    def _permitted(self) -> Self:
        if not self.allowed_slots and not self.state_permissions:
            raise ValueError('没有任何表达许可的共享正文不能下发')
        return self


class WriterStage(StrictModel):
    time_layer: Literal[TIME_LAYERS] | None
    place: str | None
    present: list[str] | None


class WriterDelivery(StrictModel):
    beat_ref: str
    presentation_instruction: str | None
    stages: list[WriterStage]


class WriterStateEvent(StrictModel):
    state_ref: str
    endpoint: Literal['established', 'ended']


class WriterKnowledgeEvent(StrictModel):
    event_ref: str
    kind: KnowledgeLinkKind
    subject_ref: PlotSubjectRef
    audience: KnowledgeSubject | None
    timing: Literal['at_entry', 'at_slot']
    slot: str | None
    allowed_slots: list[str] = Field(min_length=1)


class WriterScene(StrictModel):
    slot: str = Field(pattern=r'^s\d{2,4}$')
    purpose: str = Field(min_length=1)
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    budget_chars: int = Field(gt=0)
    deliveries: list[WriterDelivery] = Field(min_length=1)
    context_beat_refs: list[str]
    turn_beat_ref: str
    state_events: list[WriterStateEvent]
    knowledge_event_refs: list[str]
    constraint_refs: list[str]

    @model_validator(mode='after')
    def _mapped(self) -> Self:
        refs = [r.beat_ref for r in self.deliveries]
        if len(set(refs)) != len(refs) or self.turn_beat_ref not in refs:
            raise ValueError('场内节点不得重复，turn 必须属于当前呈现')
        return self
