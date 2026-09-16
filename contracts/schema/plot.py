"""完整剧情、共享证据与呈现安排；字段职责见 docs/schema/plot_blueprint.md。

只有 Beat.content 承载完整剧情；呈现指令不能成为可独立引用的子事实。
引用和范围由代码检查，语义保真由审阅负责。
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from contracts.invariants import (
    DELIVERY_MODES, NARRATIVE_PERSON, STRUCTURAL_ROLES, TEMPORAL_LINKS, TIME_LAYERS,
)
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.characters import CharacterId

NonEmpty = Annotated[str, Field(min_length=1)]
BeatId = Annotated[str, Field(pattern=r'^b\d{2,}_\d{2,}$')]
StateId = Annotated[str, Field(pattern=r'^st\d{2,}_\d{2,}$')]
EvidenceId = Annotated[str, Field(pattern=r'^e\d{2,}_\d{2,}$')]
ConstraintId = Annotated[str, Field(pattern=r'^c\d{2,}_\d{2,}$')]
SceneId = Annotated[str, Field(pattern=r'^s\d{2,4}$')]
PresentationId = Annotated[str, Field(pattern=r'^ap\d{2,}_\d{2,}$')]
LocalActorKey = Annotated[str, Field(pattern=r'^a[1-9]\d*$')]


def unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f'{label} 不得重复')


class ParagraphRange(StrictModel):
    start_para: int = Field(ge=1, strict=True)
    end_para: int = Field(ge=1, strict=True)

    @model_validator(mode='after')
    def _ordered(self) -> Self:
        if self.end_para < self.start_para:
            raise ValueError('证据止段不能早于起段')
        return self


class EvidenceAnchor(ParagraphRange):
    evidence_id: EvidenceId
    # 零起点、左闭右开；编译器由原文解析，不让模型计数。
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)

    @model_validator(mode='after')
    def _span(self) -> Self:
        if self.end <= self.start:
            raise ValueError('证据范围必须非空')
        return self


class ActorRef(StrictModel):
    """内部实体及其本次表面身份；null 只允许在消费位置无歧义时解析。"""
    char_id: CharacterId
    presentation_ref: PresentationId | None = None

    def key(self) -> tuple[str, str]:
        return self.char_id, self.presentation_ref or ''


class ActorBinding(StrictModel):
    presentation_id: PresentationId
    char_id: CharacterId
    surfaces: list[NonEmpty] = Field(min_length=1)
    evidence_refs: list[EvidenceId] = Field(min_length=1)
    public_state_ref: StateId | None = None

    @model_validator(mode='after')
    def _unique(self) -> Self:
        unique(self.surfaces, '同一表面身份的登记称谓')
        unique(self.evidence_refs, '表面身份的本章证据')
        return self


class RelationActors(StrictModel):
    subject: ActorRef
    object: ActorRef


class _PresentedSubjectRef(StrictModel):
    kind: NonEmpty
    ref: NonEmpty
    actor: ActorRef | None = None
    relation_actors: RelationActors | None = None

    @model_validator(mode='after')
    def _presentation(self) -> Self:
        if self.kind == 'character':
            if self.actor is None or self.actor.char_id != self.ref or self.relation_actors is not None:
                raise ValueError('人物主体必须携同一实体的 ActorRef')
        elif self.kind == 'relation':
            if self.relation_actors is None or self.actor is not None:
                raise ValueError('关系消费必须明确两端 ActorRef')
        elif self.actor is not None or self.relation_actors is not None:
            raise ValueError('非人物/关系主体不得夹带人物呈现')
        return self

    def raw_subject_key(self) -> tuple[str, str]:
        """仅取内部命题；实际消费去重必须使用 key()。"""
        return self.kind, self.ref

    def key(self) -> tuple[str, ...]:
        actor = self.actor.key() if self.actor else ('', '')
        subject = self.relation_actors.subject.key() if self.relation_actors else ('', '')
        object_ = self.relation_actors.object.key() if self.relation_actors else ('', '')
        return self.kind, self.ref, *actor, *subject, *object_


def unique_actors(values: list[ActorRef], label: str) -> None:
    keys = [row.key() for row in values]
    if len(keys) != len(set(keys)):
        raise ValueError(f'{label} 不得重复同一表面身份')


class PlotNarration(StrictModel):
    grammatical_person: Literal[NARRATIVE_PERSON]
    narrator: ActorRef | None
    focalizer: ActorRef | None
    pronoun_surfaces: list[NonEmpty]
    # [] 明确表示全章；多个叙述片段须显式说明适用 Beat，不按列表位置猜范围。
    beat_refs: list[BeatId]
    evidence_refs: list[EvidenceId] = Field(min_length=1)


class SourceContext(StrictModel):
    evidence_refs: list[EvidenceId] = Field(min_length=1)
    time_layer: Literal[TIME_LAYERS] | None
    place: NonEmpty | None
    # null 是未知；[] 是明确的空标注，不推断被提及的人在场。
    present: list[ActorRef] | None

    @model_validator(mode='after')
    def _unique(self) -> Self:
        unique(self.evidence_refs, '舞台证据')
        if self.present is not None:
            unique_actors(self.present, '在场人物')
        return self


class PlotBeat(StrictModel):
    beat_id: BeatId
    content: NonEmpty
    participants: list[ActorRef]
    evidence_refs: list[EvidenceId] = Field(min_length=1)
    source_contexts: list[SourceContext]

    @model_validator(mode='after')
    def _closed(self) -> Self:
        unique_actors(self.participants, '剧情参与人物')
        unique(self.evidence_refs, '剧情证据')
        for context in self.source_contexts:
            if not set(context.evidence_refs) <= set(self.evidence_refs):
                raise ValueError('舞台证据必须属于父节点')
        return self


class GlobalSubjectRef(_PresentedSubjectRef):
    kind: Literal['character', 'relation', 'world_rule', 'secret', 'setting']


class RelationUse(GlobalSubjectRef):
    """当前章实际使用的有向关系呈现，不另造 State 或事实正文。"""
    kind: Literal['relation']


class BeatMoment(StrictModel):
    kind: Literal['at_entry', 'at_beat']
    beat_ref: BeatId | None

    @model_validator(mode='after')
    def _tag(self) -> Self:
        if (self.kind == 'at_beat') != (self.beat_ref is not None):
            raise ValueError('章首锚点不带节点；节点锚点必须有 beat_ref')
        return self


class PlotState(StrictModel):
    state_id: StateId
    content: NonEmpty | None
    global_ref: GlobalSubjectRef | None
    modality: Literal['world', 'belief', 'claim', 'unresolved']
    holder: ActorRef | None
    established_at: BeatMoment
    ended_at: BeatMoment | None
    supersedes_state_refs: list[StateId]
    evidence_refs: list[EvidenceId] = Field(min_length=1)

    @model_validator(mode='after')
    def _meaning(self) -> Self:
        if (self.content is None) == (self.global_ref is None):
            raise ValueError('持续状态只保存正文或共享主体引用之一')
        if self.modality in ('belief', 'claim') and self.holder is None:
            raise ValueError('相信或声称必须明确持有者')
        if self.modality == 'world' and self.holder is not None:
            raise ValueError('客观状态不能带信念持有者')
        if self.ended_at is not None and self.ended_at.kind == 'at_entry':
            raise ValueError('当前章的状态解除须指向具体节点')
        unique(self.supersedes_state_refs, '被取代状态')
        if self.state_id in self.supersedes_state_refs:
            raise ValueError('状态不能取代自身')
        unique(self.evidence_refs, '状态证据')
        return self


class PlotRelationConstraint(StrictModel):
    constraint_id: ConstraintId
    kind: Literal['cause', 'story_before', 'response_to', 'same_scene', 'observes']
    before_beat_ref: BeatId
    after_beat_ref: BeatId
    observer: ActorRef | None
    evidence_refs: list[EvidenceId] = Field(min_length=1)

    @model_validator(mode='after')
    def _endpoints(self) -> Self:
        if self.before_beat_ref == self.after_beat_ref:
            raise ValueError('节点内部关系写在 content 中，不建立自环约束')
        if (self.kind == 'observes') != (self.observer is not None):
            raise ValueError('仅观察约束携带明确观察者')
        unique(self.evidence_refs, '约束证据')
        return self


class PlotValueConstraint(StrictModel):
    constraint_id: ConstraintId
    kind: Literal['literal', 'value', 'object']
    target_beat_ref: BeatId
    value: NonEmpty
    evidence_refs: list[EvidenceId] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique(self) -> Self:
        unique(self.evidence_refs, '约束证据')
        return self


PlotConstraint = Annotated[
    PlotRelationConstraint | PlotValueConstraint, Field(discriminator='kind')]


class InheritedStateEnding(StrictModel):
    """本章解除上章携入的状态；不改写旧定义或复制其正文。"""
    state_ref: StateId
    at_beat_ref: BeatId
    evidence_refs: list[EvidenceId] = Field(min_length=1)


class PlotBlueprint(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    source_sha256: Sha256
    chapter_function: NonEmpty
    actor_bindings: list[ActorBinding] = Field(default_factory=list)
    relation_uses: list[RelationUse] = Field(default_factory=list)
    beats: list[PlotBeat] = Field(min_length=1)
    evidence_anchors: list[EvidenceAnchor] = Field(min_length=1)
    states: list[PlotState]
    inherited_state_endings: list[InheritedStateEnding]
    constraints: list[PlotConstraint]
    # 上章状态引用原标识，不复制其正文。
    entry_state_refs: list[StateId]
    carry_out_refs: list[StateId]
    narration: list[PlotNarration] = Field(min_length=1)

    @model_validator(mode='after')
    def _closed(self) -> Self:
        beats = [r.beat_id for r in self.beats]
        states = [r.state_id for r in self.states]
        anchors = [r.evidence_id for r in self.evidence_anchors]
        constraints = [r.constraint_id for r in self.constraints]
        bindings = [r.presentation_id for r in self.actor_bindings]
        if len({row.key() for row in self.relation_uses}) != len(self.relation_uses):
            raise ValueError('关系呈现使用不能重复同一对端点')
        for values, label in ((beats, '节点'), (states, '状态'), (anchors, '证据'),
                              (constraints, '约束'), (bindings, '表面身份'), (self.entry_state_refs, '章首状态'),
                              (self.carry_out_refs, '章末状态')):
            unique(values, label)
        for prefix, values in (('b', beats), ('st', states), ('e', anchors),
                               ('c', constraints), ('ap', bindings)):
            if any(not ref.startswith(f'{prefix}{self.chapter_ordinal:02d}_')
                   for ref in values):
                raise ValueError(f'{prefix} 标识必须属于当前章')
        used_anchors: set[str] = set()
        endpoints: list[str] = []
        for row in [*self.beats, *self.states, *self.constraints, *self.inherited_state_endings,
                    *self.actor_bindings, *self.narration]:
            unique(row.evidence_refs, '对象证据')
            if not set(row.evidence_refs) <= set(anchors):
                raise ValueError('证据引用不存在或越章')
            used_anchors.update(row.evidence_refs)
        for narration in self.narration:
            unique(narration.beat_refs, '叙述范围')
            if not set(narration.beat_refs) <= set(beats):
                raise ValueError('叙述范围必须引用本章 Beat')
        if len(self.narration) > 1 and any(not row.beat_refs for row in self.narration):
            raise ValueError('多个叙述片段必须明确各自 Beat 范围')
        if used_anchors != set(anchors):
            raise ValueError('证据表只能包含实际引用的锚点')
        spans = [(r.start_para, r.end_para) for r in self.evidence_anchors]
        if len(spans) != len(set(spans)):
            raise ValueError('相同段范围必须复用一个证据锚点')
        graph = {ref: set() for ref in beats}
        strict_edges: list[tuple[str, str]] = []
        for row in self.constraints:
            if isinstance(row, PlotRelationConstraint):
                endpoints.extend((row.before_beat_ref, row.after_beat_ref))
                if row.kind in ('cause', 'story_before', 'response_to', 'observes'):
                    graph.get(row.before_beat_ref, set()).add(row.after_beat_ref)
                if row.kind in ('cause', 'story_before'):
                    strict_edges.append((row.before_beat_ref, row.after_beat_ref))
            else:
                endpoints.append(row.target_beat_ref)
        for state in self.states:
            for moment in (state.established_at, state.ended_at):
                if moment is not None and moment.beat_ref is not None:
                    endpoints.append(moment.beat_ref)
            if (state.established_at.kind == 'at_entry'
                    and state.state_id not in self.entry_state_refs):
                raise ValueError('章首成立的本地状态须列入 entry_state_refs')
            if state.ended_at is not None and state.state_id in self.carry_out_refs:
                raise ValueError('本章已解除的状态不能继续携出')
        if not set(endpoints) <= set(beats):
            raise ValueError('语义端点必须引用本章节点')
        known_states = set(states) | set(self.entry_state_refs)
        if any(row.public_state_ref is not None and row.public_state_ref not in known_states
               for row in self.actor_bindings):
            raise ValueError('表面身份的公开描述只引用本章或已携入 State')
        if not set(self.carry_out_refs) <= known_states:
            raise ValueError('章末只能携带本章或章首已有的状态')
        if any(not set(s.supersedes_state_refs) <= known_states for s in self.states):
            raise ValueError('被取代状态必须来自本章或章首')
        ended = [row.state_ref for row in self.inherited_state_endings]
        if (len(ended) != len(set(ended)) or not set(ended) <= set(self.entry_state_refs)
                or set(ended).intersection(states)):
            raise ValueError('继承状态解除只能各引用一次上章携入状态')
        replaced = [ref for state in self.states for ref in state.supersedes_state_refs]
        if len(replaced) != len(set(replaced)) or set(replaced).intersection(ended):
            raise ValueError('一个状态不能在本章被重复解除或取代')
        if set(self.carry_out_refs).intersection([*ended, *replaced]):
            raise ValueError('已解除或被取代的状态不能继续携出')
        if any(row.at_beat_ref not in beats for row in self.inherited_state_endings):
            raise ValueError('解除状态必须引用本章节点')
        state_graph = {state.state_id: set(state.supersedes_state_refs) & set(states)
                       for state in self.states}
        def visit_state(ref: str, seen: frozenset[str]) -> None:
            if ref in seen:
                raise ValueError('状态替代关系存在环')
            for parent in state_graph[ref]:
                visit_state(parent, seen | {ref})
        for ref in state_graph:
            visit_state(ref, frozenset())
        # cause/story_before 是严格先后；回应/观察允许同时。只有严格边处于
        # 可达回路才矛盾；纯非严格回路和同场关系不能被擅自升级成时间先后。
        # 这里检查已登记故事关系，完全不消费场景披露顺序。
        for before, after in strict_edges:
            pending, visited = [after], set()
            while pending:
                ref = pending.pop()
                if ref == before:
                    raise ValueError('故事因果与先后关系存在严格顺序环')
                if ref not in visited:
                    visited.add(ref)
                    pending.extend(graph[ref] - visited)
        return self


class PlotSubjectRef(_PresentedSubjectRef):
    kind: Literal['beat', 'state', 'constraint', 'character', 'relation',
                  'world_rule', 'secret', 'setting', 'chapter_boundary']


class PresentationDelivery(StrictModel):
    scene_ref: SceneId
    # 单场整体呈现显式 null；分场必须给出指令，不是子剧情正文。
    presentation_instruction: NonEmpty | None
    evidence_refs: list[EvidenceId] = Field(min_length=1)
    source_context_refs: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _unique(self) -> Self:
        unique(self.evidence_refs, '呈现证据')
        return self


class BeatPlacement(StrictModel):
    beat_ref: BeatId
    deliveries: list[PresentationDelivery] = Field(min_length=1)

    @model_validator(mode='after')
    def _distributed(self) -> Self:
        unique([r.scene_ref for r in self.deliveries], '节点的场景映射')
        if len(self.deliveries) > 1 and any(
                r.presentation_instruction is None for r in self.deliveries):
            raise ValueError('分场呈现必须说明每场承担的任务')
        return self


class PlotEndpoint(StrictModel):
    kind: Literal['state', 'constraint', 'goal']
    ref: NonEmpty
    endpoint: Literal['established', 'ended', 'before', 'after', 'target',
                      'open', 'close', 'turn', 'chapter']

    @model_validator(mode='after')
    def _kind(self) -> Self:
        allowed = {
            'state': {'established', 'ended'},
            'constraint': {'before', 'after', 'target'},
            'goal': {'open', 'close', 'turn', 'chapter'},
        }
        if self.endpoint not in allowed[self.kind]:
            raise ValueError('端点类型与主体类型不匹配')
        return self

    def key(self) -> tuple[str, str, str]:
        return self.kind, self.ref, self.endpoint


class PlotAnchorBinding(PlotEndpoint):
    scene_ref: SceneId


class PlotGoalAnchor(StrictModel):
    goal_ref: NonEmpty
    kind: Literal['open', 'close', 'turn', 'chapter']
    beat_ref: BeatId


class PlotScene(StrictModel):
    slot: SceneId
    purpose: NonEmpty
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    context_beat_refs: list[BeatId]


class PlotChapterArrangement(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    scenes: list[PlotScene] = Field(min_length=1)
    beat_placements: list[BeatPlacement] = Field(min_length=1)
    anchor_bindings: list[PlotAnchorBinding]

    @model_validator(mode='after')
    def _local(self) -> Self:
        unique([s.slot for s in self.scenes], '场景')
        unique([r.beat_ref for r in self.beat_placements], '节点 placement')
        keys = [r.key() for r in self.anchor_bindings]
        if len(keys) != len(set(keys)):
            raise ValueError('同一语义端点不能绑定两次')
        slots = {s.slot for s in self.scenes}
        if self.scenes[0].transition != 'start':
            raise ValueError('首场 transition 必须为 start')
        if any(s.transition == 'start' for s in self.scenes[1:]):
            raise ValueError('只有首场可用 start')
        for row in self.beat_placements:
            if any(d.scene_ref not in slots for d in row.deliveries):
                raise ValueError('呈现引用不存在的场景')
        if any(r.scene_ref not in slots for r in self.anchor_bindings):
            raise ValueError('端点引用不存在的场景')
        return self


class PlotNodeTombstone(StrictModel):
    node_id: NonEmpty
    subject_sha256: Sha256
    superseded_by: list[NonEmpty]
    reason: NonEmpty

    @model_validator(mode='after')
    def _targets(self) -> Self:
        unique(self.superseded_by, '替代节点')
        if self.node_id in self.superseded_by:
            raise ValueError('节点不能替代自身')
        return self


# 模型响应只交局部标识和段号；正式标识、坐标与原文摘要由编译器产生。
LocalBeatKey = Annotated[str, Field(pattern=r'^b[1-9]\d*$')]
LocalStateKey = Annotated[str, Field(pattern=r'^st[1-9]\d*$')]
LocalConstraintKey = Annotated[str, Field(pattern=r'^c[1-9]\d*$')]


class ActorRefResponse(ActorRef):
    # 只有请求明确携入的旧 presentation 才可引用正式 ID；编译器核验闭集。
    presentation_ref: LocalActorKey | PresentationId | None = None


class RelationActorsResponse(RelationActors):
    subject: ActorRefResponse
    object: ActorRefResponse


class GlobalSubjectRefResponse(GlobalSubjectRef):
    actor: ActorRefResponse | None = None
    relation_actors: RelationActorsResponse | None = None


class RelationUseResponse(GlobalSubjectRefResponse):
    kind: Literal['relation']


class ActorBindingResponse(StrictModel):
    presentation_key: LocalActorKey
    char_id: CharacterId
    surfaces: list[NonEmpty] = Field(min_length=1)
    evidence: list[ParagraphRange] = Field(min_length=1)
    public_state_ref: LocalStateKey | StateId | None = None


class PlotNarrationResponse(StrictModel):
    grammatical_person: Literal[NARRATIVE_PERSON]
    narrator: ActorRefResponse | None
    focalizer: ActorRefResponse | None
    pronoun_surfaces: list[NonEmpty]
    beat_refs: list[LocalBeatKey]
    evidence: list[ParagraphRange] = Field(min_length=1)


class SourceContextResponse(StrictModel):
    evidence: list[ParagraphRange] = Field(min_length=1)
    time_layer: Literal[TIME_LAYERS] | None
    place: NonEmpty | None
    present: list[ActorRefResponse] | None


class PlotBeatResponse(StrictModel):
    beat_key: LocalBeatKey
    content: NonEmpty
    participants: list[ActorRefResponse]
    evidence: list[ParagraphRange] = Field(min_length=1)
    source_contexts: list[SourceContextResponse]


class BeatMomentResponse(StrictModel):
    kind: Literal['at_entry', 'at_beat']
    beat_ref: LocalBeatKey | None

    @model_validator(mode='after')
    def _tag(self) -> Self:
        if (self.kind == 'at_beat') != (self.beat_ref is not None):
            raise ValueError('章首锚点不带节点；节点锚点必须有局部引用')
        return self


class PlotStateResponse(StrictModel):
    state_key: LocalStateKey
    content: NonEmpty | None
    global_ref: GlobalSubjectRefResponse | None
    modality: Literal['world', 'belief', 'claim', 'unresolved']
    holder: ActorRefResponse | None
    established_at: BeatMomentResponse
    ended_at: BeatMomentResponse | None
    # 跨章状态只能引用请求中明确提供的正式标识。
    supersedes_state_refs: list[NonEmpty]
    evidence: list[ParagraphRange] = Field(min_length=1)

    @model_validator(mode='after')
    def _meaning(self) -> Self:
        if (self.content is None) == (self.global_ref is None):
            raise ValueError('持续状态只保存正文或共享主体引用之一')
        if self.modality in ('belief', 'claim') and self.holder is None:
            raise ValueError('相信或声称必须明确持有者')
        if self.modality == 'world' and self.holder is not None:
            raise ValueError('客观状态不能带信念持有者')
        if self.ended_at is not None and self.ended_at.kind == 'at_entry':
            raise ValueError('当前章的状态解除须指向具体节点')
        return self


class PlotRelationConstraintResponse(StrictModel):
    constraint_key: LocalConstraintKey
    kind: Literal['cause', 'story_before', 'response_to', 'same_scene', 'observes']
    before_beat_ref: LocalBeatKey
    after_beat_ref: LocalBeatKey
    observer: ActorRefResponse | None
    evidence: list[ParagraphRange] = Field(min_length=1)

    @model_validator(mode='after')
    def _observer(self) -> Self:
        if (self.kind == 'observes') != (self.observer is not None):
            raise ValueError('仅观察约束携带明确观察者')
        if self.before_beat_ref == self.after_beat_ref:
            raise ValueError('节点内部关系写在 content 中，不建立自环约束')
        return self


class PlotValueConstraintResponse(StrictModel):
    constraint_key: LocalConstraintKey
    kind: Literal['literal', 'value', 'object']
    target_beat_ref: LocalBeatKey
    value: NonEmpty
    evidence: list[ParagraphRange] = Field(min_length=1)


class InheritedStateEndingResponse(StrictModel):
    state_ref: StateId
    at_beat_ref: LocalBeatKey
    evidence: list[ParagraphRange] = Field(min_length=1)


class PlotChapterResponse(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    chapter_function: NonEmpty
    actor_bindings: list[ActorBindingResponse] = Field(default_factory=list)
    relation_uses: list[RelationUseResponse] = Field(default_factory=list)
    beats: list[PlotBeatResponse] = Field(min_length=1)
    states: list[PlotStateResponse]
    inherited_state_endings: list[InheritedStateEndingResponse]
    constraints: list[Annotated[
        PlotRelationConstraintResponse | PlotValueConstraintResponse,
        Field(discriminator='kind')]]
    entry_state_refs: list[NonEmpty]
    carry_out_refs: list[NonEmpty]
    narration: list[PlotNarrationResponse] = Field(min_length=1)


class PlotBlueprintResponse(StrictModel):
    blueprints: list[PlotChapterResponse] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique(self) -> Self:
        ordinals = [str(b.chapter_ordinal) for b in self.blueprints]
        unique(ordinals, '响应章节')
        return self
