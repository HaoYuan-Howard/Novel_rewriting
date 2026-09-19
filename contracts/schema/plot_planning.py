"""无证据坐标的规划真相视图；字段与正式 Blueprint 语义相连，不新增剧情层。"""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import Field, JsonValue, model_validator
from contracts.schema.base import FrozenModel

OpaqueAlias = Annotated[str, Field(pattern=r'^[au]_[0-9a-f]{6,64}$')]
NonEmpty = Annotated[str, Field(min_length=1)]


class PlannerBeat(FrozenModel):
    alias: OpaqueAlias
    content: str = Field(min_length=1)
    participant_aliases: list[OpaqueAlias]


class PlannerCharacter(FrozenModel):
    """盲投影人物材料。

    ``surfaces`` 可为合法空：Stage2 闭集允许 first_mention_chapter 已到、登记名尚未字面
    证成的成员（典型第一人称）；此时只发 opaque alias，不得填 canonical。
    身份语义来自 Blueprint State，本投影不携带 Characters.role。
    """
    alias: OpaqueAlias
    surfaces: list[NonEmpty] = Field(default_factory=list)


class PlannerBackgroundBeat(PlannerBeat):
    """既往剧情沿用完整正文与参与者语义，不产生本章呈现义务。"""


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


class PlannerEndpoint(FrozenModel):
    endpoint_alias: OpaqueAlias
    kind: Literal['state', 'constraint']
    endpoint: Literal['established', 'ended', 'before', 'after', 'target']
    trigger_beat_alias: OpaqueAlias
    subject_aliases: list[OpaqueAlias]


class PlannerGlobalSubject(FrozenModel):
    alias: OpaqueAlias
    kind: Literal['relation', 'world_rule', 'secret', 'setting']
    value: JsonValue


class PlannerChapter(FrozenModel):
    chapter_ordinal: int = Field(ge=1)
    chapter_function: str = Field(min_length=1)
    characters: list[PlannerCharacter]
    beats: list[PlannerBeat] = Field(min_length=1)
    states: list[PlannerState]
    inherited_states: list[PlannerState]
    background_beats: list[PlannerBackgroundBeat]
    constraints: list[Annotated[PlannerRelation | PlannerValueConstraint, Field(discriminator='kind')]]
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
        characters = [row.alias for row in self.characters]
        if len(characters) != len(set(characters)) or set(characters).intersection(subjects):
            raise ValueError('规划人物身份必须唯一且不与其他主体混淆')
        referenced_characters = {ref for beat in [*self.beats, *self.background_beats]
                                 for ref in beat.participant_aliases}
        referenced_characters.update(state.holder_alias for state in [*self.states, *self.inherited_states]
                                     if state.holder_alias is not None)
        referenced_characters.update(row.observer_alias for row in self.constraints
                                     if isinstance(row, PlannerRelation) and row.observer_alias is not None)
        if not referenced_characters <= set(characters):
            raise ValueError('规划引用的人物必须带可辨认的称呼与角色语义')
        beats = {b.alias for b in self.beats}
        states = {s.alias for s in [*self.states, *self.inherited_states]}
        if not set(self.entry_state_aliases + self.carry_out_aliases) <= states:
            raise ValueError('持续状态目录未闭合')
        endpoints = [r.endpoint_alias for r in self.endpoints]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError('规划真相端点身份重复')
        if any(r.trigger_beat_alias not in beats or not set(r.subject_aliases) <= set(subjects)
               for r in self.endpoints):
            raise ValueError('必要端点必须引用本章节点与已下发主体')
        return self
