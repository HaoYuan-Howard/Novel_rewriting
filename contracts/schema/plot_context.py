"""章级 WriterVisibleView：剧情只存一份，场景与状态许可均通过引用使用。"""
from __future__ import annotations

from typing import Annotated, Literal, Self
from pydantic import Field, JsonValue, model_validator
from contracts.schema.base import StrictModel
from contracts.invariants import STRUCTURAL_ROLES, DELIVERY_MODES, TEMPORAL_LINKS, TIME_LAYERS

ActorToken = Annotated[str, Field(pattern=r'^actor_[0-9a-f]{24}$')]


class WriterSubjectRef(StrictModel):
    """写手目录身份；不携内部 char_id、呈现映射或 Global 外键。"""
    kind: Literal['character', 'relation', 'world_rule', 'secret', 'setting',
                  'beat', 'state', 'constraint', 'chapter_boundary']
    ref: str = Field(min_length=1)

    @model_validator(mode='after')
    def _public(self) -> Self:
        import re
        pattern = r'actor_[0-9a-f]{24}' if self.kind == 'character' else (
            r'subject_[0-9a-f]{24}' if self.kind in ('relation', 'world_rule', 'secret', 'setting') else None)
        if pattern and not re.fullmatch(pattern, self.ref):
            raise ValueError('Writer 人物/共享主体必须使用公开 opaque 目录身份')
        return self


class WriterAudience(StrictModel):
    kind: Literal['character', 'group']
    actor_ref: ActorToken | None = None
    label: str | None = None

    @model_validator(mode='after')
    def _closed(self) -> Self:
        if self.kind == 'character':
            if self.actor_ref is None or self.label is not None:
                raise ValueError('人物受众只携 opaque actor_ref')
        elif self.actor_ref is not None or not self.label:
            raise ValueError('群体受众只携明确标签')
        return self


class WriterBeat(StrictModel):
    beat_ref: str
    content: str = Field(min_length=1)
    participants: list[ActorToken]

    @model_validator(mode='after')
    def _unique(self) -> Self:
        if len(self.participants) != len(set(self.participants)):
            raise ValueError('同一节点不能重复同一个表面身份')
        return self


class WriterState(StrictModel):
    state_ref: str
    content: str | None
    global_ref: WriterSubjectRef | None
    modality: Literal['world', 'belief', 'claim', 'unresolved']
    holder: ActorToken | None

    @model_validator(mode='after')
    def _proposition(self) -> Self:
        if (self.content is None) == (self.global_ref is None):
            raise ValueError('状态只保存正文或主体引用之一')
        if self.modality in ('belief', 'claim') and self.holder is None:
            raise ValueError('相信或声称必须有持有者')
        if self.modality == 'world' and self.holder is not None:
            raise ValueError('客观状态不能有信念持有者')
        return self


class WriterConstraint(StrictModel):
    constraint_ref: str
    kind: Literal['cause', 'story_before', 'response_to', 'same_scene', 'observes',
                  'literal', 'value', 'object']
    before_beat_ref: str | None = None
    after_beat_ref: str | None = None
    observer: ActorToken | None = None
    target_beat_ref: str | None = None
    value: str | None = None

    @model_validator(mode='after')
    def _endpoints(self) -> Self:
        if self.kind in ('literal', 'value', 'object'):
            if not self.target_beat_ref or not self.value or any(
                    (self.before_beat_ref, self.after_beat_ref, self.observer)):
                raise ValueError('字面类约束只带目标节点与必要参数')
        elif (not self.before_beat_ref or not self.after_beat_ref
              or self.before_beat_ref == self.after_beat_ref
              or self.target_beat_ref is not None or self.value is not None
              or (self.kind == 'observes') != (self.observer is not None)):
            raise ValueError('关系约束须有两个不同端点，仅观察约束指定观察者')
        return self


class WriterStatePermission(StrictModel):
    state_ref: str
    modality: Literal['belief', 'claim', 'unresolved']
    allowed_slots: list[str] = Field(min_length=1)


class WriterGlobalSubject(StrictModel):
    subject_ref: WriterSubjectRef
    value: JsonValue
    # factual allowed_slots 为空时，仅可在 state_permissions 内以信念/声称/未知表达。
    allowed_slots: list[str]
    state_permissions: list[WriterStatePermission]

    @model_validator(mode='after')
    def _permitted(self) -> Self:
        if not self.allowed_slots and not self.state_permissions:
            raise ValueError('没有任何表达许可的共享正文不能下发')
        import re
        def validate(value):
            if isinstance(value, list):
                for row in value:
                    validate(row)
            elif isinstance(value, dict):
                if set(value) & {'char_id', 'subject_id', 'object_id', 'presentation_ref',
                                 'relation_actors', 'character_states', 'address'}:
                    raise ValueError('共享正文不得携内部人物映射或退役字段')
                for key, row in value.items():
                    if key in ('subject', 'object') and (not isinstance(row, str)
                            or re.fullmatch(r'actor_[0-9a-f]{24}', row) is None):
                        raise ValueError('共享关系的两端必须使用 opaque actor_ref')
                    validate(row)
        validate(self.value)
        if self.subject_ref.kind == 'relation' and (not isinstance(self.value, dict)
                or not {'subject', 'object', 'predicate'} <= self.value.keys()):
            raise ValueError('关系必须明确公开的有向两端及谓词')
        return self


class WriterStage(StrictModel):
    time_layer: Literal[TIME_LAYERS] | None
    place: str | None
    present: list[ActorToken] | None


class WriterDelivery(StrictModel):
    beat_ref: str
    presentation_instruction: str | None
    stages: list[WriterStage]


class WriterStateEvent(StrictModel):
    state_ref: str
    endpoint: Literal['established', 'ended']


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
    constraint_refs: list[str]

    @model_validator(mode='after')
    def _mapped(self) -> Self:
        refs = [r.beat_ref for r in self.deliveries]
        if len(set(refs)) != len(refs) or self.turn_beat_ref not in refs:
            raise ValueError('场内节点不得重复，turn 必须属于当前呈现')
        return self
