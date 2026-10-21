"""story-v3 目标故事契约：语义 wire 与正式目标真相分离。

编号、预算、章边界快照及章包由 compiler 派生；模型只声明语义与引用选择。
TargetModelDraft / ChapterPlan 是本层内部材料。TargetStory 是唯一目标真源；
下游写手只读正式投影的当章包与 Hook 包，不读全书自然文本。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.source_story import StoryVersion

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Version = StoryVersion
Position = Annotated[int, Field(strict=True, ge=1)]


class TargetNameChoice(StrictModel):
    entity_ref: Text
    action: Literal['rename', 'keep']
    family: str | None = None
    given: str | None = None
    aliases: dict[str, str] = Field(default_factory=dict)
    reason: str = ''


class NamingWire(StrictModel):
    protocol_version: Literal['story-naming-v1']
    entries: tuple[TargetNameChoice, ...]


class TargetName(StrictModel):
    entity_id: Text
    primary: Text
    surfaces: dict[str, str]


class NameMap(StrictModel):
    contract_version: Version
    names: dict[str, TargetName]


class RequirementWire(StrictModel):
    kind: Literal['plot', 'character_arc', 'relation', 'cause', 'revelation',
                  'initial', 'world', 'ending']
    statement: Text
    source_refs: tuple[Text, ...] = Field(min_length=1)
    participants: tuple[str, ...]
    required: bool
    satisfaction: Text
    requires: tuple[Position, ...]
    constraints: tuple[Text, ...]
    details: tuple[Text, ...]


class PreservationRequirement(StrictModel):
    requirement_id: Text
    kind: str
    statement: Text
    participants: tuple[str, ...]
    required: bool
    satisfaction: Text
    requires: tuple[str, ...]
    constraints: tuple[str, ...]
    details: tuple[str, ...]


class WorldRuleDecision(StrictModel):
    rule_ref: Text
    action: Literal['keep', 'adapt', 'omit']
    target_statement: str | None = None
    reason: str = ''

    @model_validator(mode='after')
    def _decision(self) -> 'WorldRuleDecision':
        if self.action in ('keep', 'adapt') and not (self.target_statement or '').strip():
            raise ValueError('keep/adapt 世界规则必须用目标称谓给 target_statement')
        if self.action == 'omit' and self.target_statement is not None:
            raise ValueError('omit 不得写 target_statement')
        if self.action != 'keep' and not self.reason.strip():
            raise ValueError('adapt/omit 世界规则必须给 reason')
        return self


class ConsistencyDecision(StrictModel):
    conflict_no: Position
    action: Literal['preserve_ambiguity', 'local_consistency']
    explanation: Text
    interpretation: str | None = None
    protected_outcomes: tuple[Text, ...]

    @model_validator(mode='after')
    def _interpretation(self) -> 'ConsistencyDecision':
        if self.action == 'local_consistency' and not (self.interpretation or '').strip():
            raise ValueError('局部一致化必须明写选定解释')
        if self.action == 'preserve_ambiguity' and self.interpretation is not None:
            raise ValueError('保留含混不得暗中选定解释')
        return self


class SourceFactIssue(StrictModel):
    source_ref: Text
    path: tuple[str | Annotated[int, Field(strict=True, ge=0)], ...]
    operation: Literal['replace', 'remove', 'append']
    kind: Literal['missing', 'contradiction', 'unsupported']
    statement: Text
    evidence_refs: tuple[Text, ...] = Field(min_length=1)


class PreserveWire(StrictModel):
    protocol_version: Literal['story-preserve-v1']
    requirements: tuple[RequirementWire, ...] = Field(min_length=1)
    world_rules: tuple[WorldRuleDecision, ...]
    consistency_decisions: tuple[ConsistencyDecision, ...]
    rewrite_direction: Text
    expression: Text
    source_issues: tuple[SourceFactIssue, ...]


class TargetWorldRule(StrictModel):
    rule_id: Text
    statement: Text


class PreservationContract(StrictModel):
    contract_version: Version
    requirements: tuple[PreservationRequirement, ...]
    world_rules: tuple[TargetWorldRule, ...]
    world_rule_decisions: tuple[WorldRuleDecision, ...]
    consistency_decisions: tuple[ConsistencyDecision, ...]
    rewrite_direction: Text
    expression: Text
    # 审计映射不进入 Design 简报；不构成第二份源事实。
    source_bindings: dict[str, tuple[str, ...]]


class StoryState(StrictModel):
    """持续状态；同一主体/维度/限定对象的不同模态分别占槽。

    计划和条件不能覆盖实际状态。实现计划后若需关闭旧计划，须显式解除
    planned 槽；compiler 不从自然语言推断计划已实现或已取消。
    """
    subject_ids: tuple[Text, ...]
    dimension: Text
    qualifier: str = ''
    value: Text | None
    op: Literal['set', 'unset', 'unknown']
    modality: Literal['actual', 'planned', 'expected', 'conditional', 'unknown']

    @model_validator(mode='after')
    def _operation(self) -> 'StoryState':
        if (self.op == 'set') != (self.value is not None):
            raise ValueError('set必须给value；unset/unknown必须显式value=null')
        return self


class TargetProposition(StrictModel):
    statement: Text
    status: Literal['established', 'refuted', 'undetermined']


class KnowledgeWire(StrictModel):
    holder: Text
    proposition_no: Position
    mode: Literal['known', 'believed', 'suspected', 'heard', 'refuted']


class TargetKnowledge(StrictModel):
    holder: Text
    proposition_ref: Text
    mode: Literal['known', 'believed', 'suspected', 'heard', 'refuted']


class TargetEventWire(StrictModel):
    summary: Text
    participants: tuple[Text, ...] = Field(min_length=1)
    trigger: Text
    choice: Text
    result: Text
    requires: tuple[Position, ...]
    after: tuple[Position, ...]
    temporal_frame: Literal['current', 'past', 'future', 'summary', 'unknown']
    realizes: tuple[str, ...]
    state_changes: tuple[StoryState, ...]
    knowledge_reads: tuple[KnowledgeWire, ...]
    knowledge_changes: tuple[KnowledgeWire, ...]
    name_releases: dict[str, Text]


class TargetEvent(StrictModel):
    event_id: Text
    summary: Text
    participants: tuple[str, ...]
    trigger: Text
    choice: Text
    result: Text
    requires: tuple[str, ...]
    after: tuple[str, ...]
    temporal_frame: Literal['current', 'past', 'future', 'summary', 'unknown']
    realizes: tuple[str, ...]
    state_changes: tuple[StoryState, ...]
    knowledge_reads: tuple[TargetKnowledge, ...]
    knowledge_changes: tuple[TargetKnowledge, ...]
    name_releases: dict[str, str]


class CharacterArcWire(StrictModel):
    entity_ref: Text
    starting_position: Text
    pressure: Text
    choice: Text
    event_nos: tuple[Position, ...] = Field(min_length=1)
    end_position: Text


class CharacterArc(StrictModel):
    entity_ref: Text
    starting_position: Text
    pressure: Text
    choice: Text
    event_refs: tuple[str, ...]
    end_position: Text


class TargetChapterWire(StrictModel):
    purpose: Text
    event_nos: tuple[Position, ...] = Field(min_length=1)
    entry: Text
    exit: Text
    weight: float = Field(default=1.0, gt=0, allow_inf_nan=False)


class TargetModelWire(StrictModel):
    protocol_version: Literal['story-model-v1']
    premise: Text
    narration: Text
    style: Text
    character_voices: dict[str, Text]
    character_arcs: tuple[CharacterArcWire, ...]
    propositions: tuple[TargetProposition, ...]
    initial_state: tuple[StoryState, ...]
    initial_knowledge: tuple[KnowledgeWire, ...]
    initial_displays: dict[str, Text]
    events: tuple[TargetEventWire, ...] = Field(min_length=1)
    chapters: tuple[TargetChapterWire, ...] = Field(min_length=1)


class TargetChapter(StrictModel):
    chapter_ordinal: Position
    purpose: Text
    event_refs: tuple[str, ...]
    entry: Text
    exit: Text
    length_budget: Position
    participants: tuple[str, ...]
    entry_state: tuple[StoryState, ...]
    exit_state: tuple[StoryState, ...]
    entry_knowledge: tuple[TargetKnowledge, ...]
    exit_knowledge: tuple[TargetKnowledge, ...]
    entry_displays: dict[str, str]
    exit_displays: dict[str, str]


class TargetModelDraft(StrictModel):
    contract_version: Version
    source_chapter_count: Position
    total_length_budget: Position
    premise: Text
    narration: Text
    style: Text
    character_voices: dict[str, str]
    character_arcs: tuple[CharacterArc, ...]
    world_rules: tuple[TargetWorldRule, ...]
    propositions: dict[str, TargetProposition]
    initial_state: tuple[StoryState, ...]
    initial_knowledge: tuple[TargetKnowledge, ...]
    initial_displays: dict[str, str]
    events: tuple[TargetEvent, ...]
    chapters: tuple[TargetChapter, ...]

    @model_validator(mode='after')
    def _canonical_closure(self) -> 'TargetModelDraft':
        _validate_model(self)
        return self


class ChapterActionWire(StrictModel):
    # 本章 event_nos 的 1-based 局部引用；None 是无跨章效应的连接性行动。
    event_no: Position | None
    trigger: Text
    choice: Text
    action: Text
    response: Text
    result: Text
    transition: Text


class SnapshotCue(StrictModel):
    """引用当前章边界列表中的一项；text由语义owner按本章显示边界表达。"""
    item_no: Position
    text: Text


class WorldRuleCue(StrictModel):
    rule_ref: Text
    text: Text


class RequirementCue(StrictModel):
    requirement_ref: Text
    placement: Literal['respect', 'background']
    text: Text


class WriterContext(StrictModel):
    """当章可送达的自然表达；全书真相仍为TargetStory的结构化模型。

    没有选择的真相不自动送达。禁止在安全提示中包含后章秘密的答案；
    compiler只解析引用，不从裸文本替换或猜测人物指称。
    """
    purpose: Text
    entry: Text
    exit: Text
    narration: Text
    style: Text
    entry_state: tuple[SnapshotCue, ...]
    exit_state: tuple[SnapshotCue, ...]
    entry_knowledge: tuple[SnapshotCue, ...]
    world_rules: tuple[WorldRuleCue, ...]
    requirements: tuple[RequirementCue, ...]
    voices: dict[str, Text]


class ChapterScene(StrictModel):
    setting: Text
    time: Text
    pov: Text
    participants: tuple[Text, ...] = Field(min_length=1)
    goal: Text
    opposition: Text
    turn: Text
    realization: Text
    actions: tuple[ChapterActionWire, ...] = Field(min_length=1)


class ChapterPlanWire(StrictModel):
    protocol_version: Literal['story-chapter-v1']
    scenes: tuple[ChapterScene, ...] = Field(min_length=1)
    writer_context: WriterContext
    # 非空代表发现骨架缺口；compiler 拒发并交回目标模型 owner。
    cross_boundary_changes: tuple[StoryState, ...]
    model_issues: tuple['TargetModelIssue', ...]


class TargetModelIssue(StrictModel):
    event_no: Position | None
    kind: Literal['missing_cause', 'missing_knowledge', 'inconsistent_boundary', 'missing_fact']
    component: Literal['summary', 'trigger', 'choice', 'result', 'requires', 'after',
        'state_changes', 'knowledge_reads', 'knowledge_changes', 'propositions',
        'initial_state', 'initial_knowledge', 'chapter_entry', 'chapter_exit']
    operation: Literal['replace', 'append']
    item_no: Position | None
    statement: Text

    @model_validator(mode='after')
    def explicit_scope(self) -> 'TargetModelIssue':
        global_fields = ('propositions', 'initial_state', 'initial_knowledge', 'chapter_entry', 'chapter_exit')
        arrays = ('requires', 'after', 'state_changes', 'knowledge_reads', 'knowledge_changes',
                  'propositions', 'initial_state', 'initial_knowledge')
        if (self.component in global_fields) != (self.event_no is None):
            raise ValueError('全书/章边界组件须event_no=null，事件组件必须引用本章事件')
        if self.component in arrays:
            if (self.operation == 'append') != (self.item_no is None):
                raise ValueError('数组append须item_no=null，replace须明确既有条目序号')
        elif self.operation != 'replace' or self.item_no is not None:
            raise ValueError('标量组件只能replace且item_no=null')
        return self


class ChapterPlan(StrictModel):
    contract_version: Version
    chapter_ordinal: Position
    scenes: tuple[ChapterScene, ...]
    writer_context: WriterContext


class TargetStory(TargetModelDraft):
    names: dict[str, TargetName]
    chapter_plans: tuple[ChapterPlan, ...]
    preservation: tuple[PreservationRequirement, ...]

    @model_validator(mode='after')
    def _complete_story(self) -> 'TargetStory':
        if set(self.names) != set(self.initial_displays):
            raise ValueError('TargetStory名称表与实体闭集不一致')
        if tuple(p.chapter_ordinal for p in self.chapter_plans) != tuple(range(1, self.source_chapter_count + 1)):
            raise ValueError('TargetStory必须有全部有序ChapterPlan')
        for chapter, plan in zip(self.chapters, self.chapter_plans):
            _validate_writer_context(self, chapter, plan.writer_context)
            refs = [action.event_no for scene in plan.scenes for action in scene.actions
                    if action.event_no is not None]
            if refs != list(range(1, len(chapter.event_refs) + 1)):
                raise ValueError(f'第{chapter.chapter_ordinal}章的正式行动不完整或顺序改变')
            for scene in plan.scenes:
                if set(scene.participants) - set(chapter.participants):
                    raise ValueError('ChapterPlan新增未授权参与者')
                if scene.pov not in (*chapter.participants, 'narrator'):
                    raise ValueError('ChapterPlan视角引用未知实体')
        requirements = {r.requirement_id: r for r in self.preservation}
        if len(requirements) != len(self.preservation):
            raise ValueError('保存要求ID重复')
        realized = {ref: [] for ref in requirements}
        for event in self.events:
            for ref in event.realizes:
                if ref not in realized:
                    raise ValueError('目标事件引用未知保存要求')
                realized[ref].append(event.event_id)
        for ref, requirement in requirements.items():
            if requirement.required and not realized[ref]:
                raise ValueError(f'必保要求{ref}缺少目标承载')
            if set(requirement.requires) - set(requirements):
                raise ValueError('保存要求的依赖未闭合')
        _dag({ref: r.requires for ref, r in requirements.items()}, '保存要求依赖')
        return self


class AdaptationEntry(StrictModel):
    requirement_id: Text
    source_refs: tuple[str, ...]
    target_event_refs: tuple[str, ...]
    status: Literal['realized', 'optional_omitted']


class AdaptationMap(StrictModel):
    contract_version: Version
    entries: tuple[AdaptationEntry, ...]
    consistency_decisions: tuple[ConsistencyDecision, ...]
    world_rule_decisions: tuple[WorldRuleDecision, ...]


class ChapterPacket(StrictModel):
    contract_version: Version
    chapter_ordinal: Position
    source_chapter_count: Position
    length_budget: Position
    min_chars: Position
    max_chars: Position
    participants: tuple[str, ...]
    names: dict[str, str]
    # 安全的当章状态提示，不充当结构化状态真源。
    entry_state: tuple[str, ...]
    exit_state: tuple[str, ...]
    actions: tuple[str, ...]
    must_realize: tuple[str, ...]
    must_respect: tuple[str, ...]
    continuity: tuple[str, ...]
    creative_guidance: tuple[str, ...]
    background: tuple[str, ...]
    guard_only: tuple[str, ...]

    @model_validator(mode='after')
    def _boundary(self) -> 'ChapterPacket':
        if self.chapter_ordinal > self.source_chapter_count:
            raise ValueError('章包chapter_ordinal越出source_chapter_count')
        if not self.min_chars <= self.length_budget <= self.max_chars:
            raise ValueError('章包篇幅须满足min_chars≤length_budget≤max_chars')
        if set(self.participants) != set(self.names):
            raise ValueError('章包names必须恰好覆盖participants')
        return self


class HookBeatWire(StrictModel):
    event_no: Position
    trigger: Text
    choice: Text
    action: Text
    result: Text


class HookBeat(StrictModel):
    event_ref: Text
    trigger: Text
    choice: Text
    action: Text
    result: Text


class HookWriterContext(StrictModel):
    style: Text
    knowledge: tuple[SnapshotCue, ...]
    world_rules: tuple[WorldRuleCue, ...]


class HookDesignWire(StrictModel):
    protocol_version: Literal['story-hook-v1']
    beats: tuple[HookBeatWire, ...] = Field(min_length=1)
    writer_context: HookWriterContext
    organization: Text
    emphasis: Text
    ending: Text
    reader_expectation: Text
    allowed_knowledge: tuple[KnowledgeWire, ...]
    allowed_displays: dict[str, Text]


class HookPlan(StrictModel):
    contract_version: Version
    event_refs: tuple[str, ...]
    beats: tuple[HookBeat, ...]
    writer_context: HookWriterContext
    organization: Text
    emphasis: Text
    ending: Text
    reader_expectation: Text
    allowed_knowledge: tuple[TargetKnowledge, ...]
    allowed_displays: dict[str, str]
    length_budget: Position


class HookPacket(StrictModel):
    contract_version: Version
    length_budget: Position
    min_chars: Position
    max_chars: Position
    names: dict[str, str]
    must_realize: tuple[str, ...]
    must_respect: tuple[str, ...]
    continuity: tuple[str, ...]
    creative_guidance: tuple[str, ...]
    background: tuple[str, ...]
    guard_only: tuple[str, ...]

    @model_validator(mode='after')
    def _boundary(self) -> 'HookPacket':
        if not self.min_chars <= self.length_budget <= self.max_chars:
            raise ValueError('Hook篇幅须满足min_chars≤length_budget≤max_chars')
        return self


def _dag(edges, where):
    pending = {key: set(values) for key, values in edges.items()}
    while pending:
        ready = {key for key, values in pending.items() if not values}
        if not ready:
            raise ValueError(f'{where}含环或悬空引用')
        pending = {key: values - ready for key, values in pending.items() if key not in ready}


def writer_context_issues(model: TargetModelDraft, chapter: TargetChapter,
                          context: WriterContext) -> tuple[tuple[tuple, str], ...]:
    """只检查送达提示的引用和scope；自然文本的语义忠实度保持unver。"""
    problems = []
    participants = set(chapter.participants)
    for field, snapshot in (('entry_state', chapter.entry_state), ('exit_state', chapter.exit_state),
                            ('entry_knowledge', chapter.entry_knowledge)):
        seen = set()
        for index, cue in enumerate(getattr(context, field)):
            path = ('writer_context', field, index, 'item_no')
            if cue.item_no in seen or cue.item_no > len(snapshot):
                problems.append((path, '边界提示引用重复或越界'))
                continue
            seen.add(cue.item_no)
            item = snapshot[cue.item_no - 1]
            if field == 'entry_knowledge':
                valid = item.holder == 'reader' or item.holder in participants
            else:
                valid = not set(item.subject_ids) - participants
            if not valid:
                problems.append((path, '边界提示引用了本章未参与主体'))
    rules = {rule.rule_id for rule in model.world_rules}
    events = {event.event_id: event for event in model.events}
    requirements = {ref for event_ref in chapter.event_refs for ref in events[event_ref].realizes}
    for field, refs, key in (('world_rules', rules, 'rule_ref'),
                              ('requirements', requirements, 'requirement_ref')):
        seen = set()
        for index, cue in enumerate(getattr(context, field)):
            ref = getattr(cue, key)
            if ref not in refs or ref in seen:
                problems.append((('writer_context', field, index, key), '局部提示引用未知、越章或重复'))
            seen.add(ref)
    for entity in set(context.voices) - participants:
        problems.append((('writer_context', 'voices', entity), '声线提示引用了本章未参与主体'))
    return tuple(problems)


def _validate_writer_context(model, chapter, context):
    problems = writer_context_issues(model, chapter, context)
    if problems:
        raise ValueError('；'.join(message for _path, message in problems))


def _validate_model(model: TargetModelDraft) -> None:
    """读取canonical时也重验引用和折叠结果，文件存在不代表曾编译成功。"""
    entities = set(model.initial_displays)
    event_of = {e.event_id: e for e in model.events}
    if len(event_of) != len(model.events) or not event_of:
        raise ValueError('目标事件ID必须非空且唯一')
    if len(model.chapters) != model.source_chapter_count or tuple(c.chapter_ordinal for c in model.chapters) != tuple(range(1, model.source_chapter_count + 1)):
        raise ValueError('目标章号必须完整连续并等于源章数')
    if sum(c.length_budget for c in model.chapters) != model.total_length_budget:
        raise ValueError('目标章节预算合计不等于全书预算')
    flattened = tuple(ref for chapter in model.chapters for ref in chapter.event_refs)
    if len(flattened) != len(set(flattened)) or set(flattened) != set(event_of):
        raise ValueError('目标事件归章必须完整且互斥')
    if set(model.character_voices) - entities:
        raise ValueError('目标声线引用未知实体')
    for arc in model.character_arcs:
        if arc.entity_ref not in entities or set(arc.event_refs) - set(event_of):
            raise ValueError('目标人物弧引用未闭合')
    def state_key(state):
        return state.subject_ids, state.dimension, state.qualifier, state.modality
    def state_rows(rows):
        keys = [state_key(row) for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('同模态的具体状态槽重复；多目标请用不同qualifier')
        if any(set(row.subject_ids) - entities or len(row.subject_ids) != len(set(row.subject_ids)) for row in rows):
            raise ValueError('状态主体未知或重复')
    def knowledge_rows(rows):
        keys = [(row.holder, row.proposition_ref) for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('同一认知位置存在冲突模式')
        for row in rows:
            if row.holder not in entities | {'reader'} or row.proposition_ref not in model.propositions:
                raise ValueError('认知持有者/命题引用未闭合')
            if row.mode == 'known' and model.propositions[row.proposition_ref].status != 'established':
                raise ValueError('未确立/已推翻命题不能成为确知')
    state_rows(model.initial_state)
    if any(s.op == 'unset' for s in model.initial_state):
        raise ValueError('初始状态不能解除尚未建立的状态槽')
    knowledge_rows(model.initial_knowledge)
    for event in model.events:
        if set(event.participants) - entities or len(event.participants) != len(set(event.participants)):
            raise ValueError('目标事件参与者未知或重复')
        if set((*event.requires, *event.after)) - set(event_of):
            raise ValueError('目标因果/时间引用未闭合')
        if set(event.name_releases) - set(event.participants):
            raise ValueError('揭名主体必须参与事件')
        state_rows(event.state_changes)
        knowledge_rows(event.knowledge_reads)
        knowledge_rows(event.knowledge_changes)
    _dag({ref: (*event.requires, *event.after) for ref, event in event_of.items()}, '目标因果/时间')
    state = {state_key(s): s for s in model.initial_state}
    knowledge = {(k.holder, k.proposition_ref): k for k in model.initial_knowledge}
    displays = dict(model.initial_displays)
    for chapter in model.chapters:
        if chapter.entry_state != tuple(state.values()) or chapter.entry_knowledge != tuple(knowledge.values()) or chapter.entry_displays != displays:
            raise ValueError(f'第{chapter.chapter_ordinal}章入场边界不等于正式前态')
        participants = tuple(dict.fromkeys(p for ref in chapter.event_refs for p in event_of[ref].participants))
        if chapter.participants != participants:
            raise ValueError('章节参与者不是其目标事件的确定性并集')
        for ref in chapter.event_refs:
            event = event_of[ref]
            for read in event.knowledge_reads:
                if event.temporal_frame == 'current' or read.holder == 'reader':
                    if knowledge.get((read.holder, read.proposition_ref)) != read:
                        raise ValueError(f'{ref}认知读取不满足其前提')
            if event.temporal_frame == 'current':
                for change in event.state_changes:
                    if change.op == 'unset':
                        if state_key(change) not in state:
                            raise ValueError('不能解除不存在的目标状态槽')
                        del state[state_key(change)]
                    else:
                        state[state_key(change)] = change
            knowledge.update({(k.holder, k.proposition_ref): k for k in event.knowledge_changes
                              if event.temporal_frame == 'current' or k.holder == 'reader'})
            displays.update(event.name_releases)
        if chapter.exit_state != tuple(state.values()) or chapter.exit_knowledge != tuple(knowledge.values()) or chapter.exit_displays != displays:
            raise ValueError(f'第{chapter.chapter_ordinal}章出场边界不等于正式事件折叠')
