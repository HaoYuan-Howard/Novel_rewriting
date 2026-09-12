"""无证据坐标的规划真相视图；字段与正式 Blueprint 语义相连，不新增剧情层。"""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import Field, JsonValue, model_validator
from contracts.schema.base import FrozenModel

OpaqueAlias = Annotated[str, Field(pattern=r'^[au]_[0-9a-f]{6,64}$')]


class PlannerBeat(FrozenModel):
    alias: OpaqueAlias
    content: str = Field(min_length=1)
    participant_aliases: list[OpaqueAlias]


class PlannerBackgroundBeat(FrozenModel):
    alias: OpaqueAlias
    content: str = Field(min_length=1)


class PlannerState(FrozenModel):
    alias: OpaqueAlias
    content: str | None
    modality: Literal['world', 'belief', 'claim', 'unresolved']
    holder_alias: OpaqueAlias | None
    global_subject_alias: OpaqueAlias | None
    established_at_entry: bool | None = None
    supersedes_state_aliases: list[OpaqueAlias] = Field(default_factory=list)

    @model_validator(mode='after')
    def _one_content(self):
        if (self.content is None) == (self.global_subject_alias is None):
            raise ValueError('状态正文与共享引用必须二选一')
        return self


class PlannerRelation(FrozenModel):
    alias: OpaqueAlias
    kind: Literal['cause', 'story_before', 'response_to', 'same_scene', 'observes']
    before_beat_alias: OpaqueAlias
    after_beat_alias: OpaqueAlias
    observer_alias: OpaqueAlias | None


class PlannerValueConstraint(FrozenModel):
    alias: OpaqueAlias
    kind: Literal['literal', 'value', 'object']
    target_beat_alias: OpaqueAlias
    value: str = Field(min_length=1)


class PlannerAudience(FrozenModel):
    kind: Literal['character', 'group']
    char_id: OpaqueAlias | None = None
    label: str | None = None


class PlannerKnowledge(FrozenModel):
    event_alias: OpaqueAlias
    subject_alias: OpaqueAlias
    kind: Literal['secret_reveal', 'secret_setup', 'knowledge_event', 'world_rule',
                  'world_rule_reveal', 'relation', 'relation_visibility',
                  'character_state', 'setting', 'state_reveal']
    audience: PlannerAudience | None
    timing: Literal['at_entry', 'at_beats']
    trigger_beat_alias: OpaqueAlias | None = None

    @model_validator(mode='after')
    def _trigger(self):
        if (self.timing == 'at_beats') != (self.trigger_beat_alias is not None):
            raise ValueError('知识触发节点与时点类型不匹配')
        return self


class PlannerEndpoint(FrozenModel):
    endpoint_alias: OpaqueAlias
    kind: Literal['state', 'constraint', 'knowledge']
    endpoint: Literal['established', 'ended', 'before', 'after', 'target', 'trigger']
    trigger_beat_alias: OpaqueAlias
    subject_alias: OpaqueAlias


class PlannerGlobalSubject(FrozenModel):
    alias: OpaqueAlias
    kind: Literal['relation', 'world_rule', 'secret', 'setting', 'character_state']
    value: JsonValue


class PlannerChapter(FrozenModel):
    chapter_ordinal: int = Field(ge=1)
    chapter_function: str = Field(min_length=1)
    beats: list[PlannerBeat] = Field(min_length=1)
    states: list[PlannerState]
    inherited_states: list[PlannerState]
    background_beats: list[PlannerBackgroundBeat]
    constraints: list[Annotated[PlannerRelation | PlannerValueConstraint, Field(discriminator='kind')]]
    knowledge: list[PlannerKnowledge]
    endpoints: list[PlannerEndpoint]
    entry_state_aliases: list[OpaqueAlias]
    carry_out_aliases: list[OpaqueAlias]
    global_subjects: list[PlannerGlobalSubject]

    @model_validator(mode='after')
    def _closed(self):
        subjects = [row.alias for field in (self.beats, self.states, self.inherited_states,
                    self.background_beats, self.constraints, self.global_subjects) for row in field]
        if len(subjects) != len(set(subjects)):
            raise ValueError('规划真相主体不能重复保存')
        beats = {b.alias for b in self.beats}
        states = {s.alias for s in [*self.states, *self.inherited_states]}
        if not set(self.entry_state_aliases + self.carry_out_aliases) <= states:
            raise ValueError('持续状态目录未闭合')
        endpoints = [r.endpoint_alias for r in self.endpoints]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError('规划真相端点身份重复')
        if any(r.trigger_beat_alias not in beats or r.subject_alias not in subjects
               for r in self.endpoints):
            raise ValueError('必要端点必须引用本章节点与已下发主体')
        return self
