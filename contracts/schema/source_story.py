"""story-v1 源故事：局部观察、整书综合与唯一正式事实契约。

命题的世界地位、人物认识、读者披露是正交关系。实体内部标签不授予揭名；
阅读序不充当故事时间；状态分类不充当具体状态槽。观察中的语义标签由模型选择，
一切机械句柄与正式 ID 由 compiler 派发。本模块只定义形状与引用闭合，不证明语义。
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel

STORY_VERSION = 'story-v1'
StoryVersion = Literal['story-v1']
EntityKind = Literal['person', 'place', 'object']
WorldStatus = Literal['established', 'refuted', 'undetermined']
KnowledgeMode = Literal[
    'learns', 'suspects', 'confirms', 'hears_claim', 'believes', 'asserts',
    'rejects', 'retracts',
]
Polarity = Literal['affirmative', 'negative', 'undetermined']
TemporalFrame = Literal['current', 'past', 'future', 'summary', 'unknown']
EvidenceRefs = Annotated[tuple[str, ...], Field(min_length=1)]


class SourceEvidence(StrictModel):
    model_config = {'extra': 'forbid', 'frozen': True}
    chapter_ordinal: int = Field(ge=0, strict=True)
    paragraph_ordinal: int = Field(ge=1)
    text: str = Field(min_length=1)


class StoryIssue(StrictModel):
    code: str
    owner: str
    item: str
    path: tuple[str | int, ...]
    message: str


class StoryTemporal(StrictModel):
    frame: TemporalFrame
    anchor_event: str | None


class StorySurface(StrictModel):
    literal: str = Field(min_length=1)
    evidence_refs: EvidenceRefs


class StoryNameStructure(StrictModel):
    family_group: str | None
    family: str | None
    given: str | None
    aliases: dict[str, str]
    surface_forms: dict[str, str | None]

    @model_validator(mode='after')
    def _forms_render(self):
        def replace(match):
            token = match.group(1)
            if token == 'family' and self.family:
                return self.family
            if token == 'given' and self.given:
                return self.given
            if token.startswith('alias:') and token[6:] in self.aliases:
                return self.aliases[token[6:]]
            raise ValueError(f'姓名模板引用未知部件 {token!r}')
        if any(not literal for literal in self.aliases.values()):
            raise ValueError('化名字面不得为空')
        for surface, form in self.surface_forms.items():
            if form is not None and re.sub(r'\{([^{}]+)\}', replace, form) != surface:
                raise ValueError(f'姓名模板未逐字还原称谓 {surface!r}')
        return self


class StoryEntity(StrictModel):
    kind: EntityKind
    label: str = Field(min_length=1)
    surfaces: tuple[StorySurface, ...] = Field(min_length=1)
    name: StoryNameStructure | None = None


class StoryEvent(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    order: int = Field(ge=1)
    summary: str = Field(min_length=1)
    participants: tuple[str, ...]
    temporal: StoryTemporal
    scene_boundary: Literal['new', 'continue', 'unknown']
    evidence_refs: EvidenceRefs


class StoryProposition(StrictModel):
    statement: str = Field(min_length=1)
    entity_refs: tuple[str, ...]
    world_status: WorldStatus
    evidence_refs: EvidenceRefs


class KnowledgeWire(StrictModel):
    holder: str = Field(min_length=1)
    proposition_ref: str = Field(min_length=1)
    mode: KnowledgeMode
    polarity: Polarity
    at: str = Field(min_length=1)
    supersedes: tuple[str, ...]
    evidence_refs: EvidenceRefs


class StoryKnowledgeUpdate(KnowledgeWire):
    update_id: str


class StoryExposure(StrictModel):
    event_ref: str
    proposition_ref: str
    mode: Literal['confirmed', 'claimed', 'suggested']
    evidence_refs: EvidenceRefs


class StoryStateSlot(StrictModel):
    subjects: tuple[str, ...] = Field(min_length=1)
    aspect: str = Field(min_length=1)
    focus: str = Field(min_length=1)


class StateUpdateWire(StrictModel):
    slot_ref: str
    op: Literal['set', 'unset', 'unknown']
    value: str | None = None
    proposition_ref: str | None = None
    at: str
    effective_time: Literal['current', 'past', 'future', 'unknown']
    modality: Literal['actual', 'planned', 'expected', 'conditional']
    persistence: Literal['stable_until_changed', 'scene', 'unknown']
    evidence_refs: EvidenceRefs

    @model_validator(mode='after')
    def _operation(self):
        if (self.op == 'set') != (self.value is not None):
            raise ValueError('set 必须给 value；unset/unknown 不得带 value')
        return self


class StoryStateUpdate(StateUpdateWire):
    update_id: str


class StoryCausalLink(StrictModel):
    from_event: str
    to_event: str
    kind: Literal['causes', 'motivates', 'enables', 'setup_payoff']
    support: Literal['stated', 'inferred']
    evidence_refs: EvidenceRefs


class StoryThreadUpdate(StrictModel):
    event_ref: str
    op: Literal['open', 'progress', 'resolve', 'abandon']
    resolution: str | None = None
    evidence_refs: EvidenceRefs

    @model_validator(mode='after')
    def _resolution(self):
        if self.op == 'resolve' and not self.resolution:
            raise ValueError('resolve 必须给出 resolution')
        if self.op == 'open' and self.resolution is not None:
            raise ValueError('open 不得带 resolution')
        return self


class StoryThread(StrictModel):
    question: str = Field(min_length=1)
    updates: tuple[StoryThreadUpdate, ...] = Field(min_length=1)


class StoryWorldRule(StrictModel):
    kind: Literal['hard_rule', 'social_norm', 'institutional_fact', 'setting_constant']
    statement: str = Field(min_length=1)
    evidence_refs: EvidenceRefs


class StoryExpression(StrictModel):
    person: Literal['first', 'third', 'unknown']
    focalizers: tuple[str, ...]
    statement: str = Field(min_length=1)
    mechanisms: tuple[str, ...]
    evidence_refs: EvidenceRefs


class StoryConflict(StrictModel):
    kind: str
    statement: str = Field(min_length=1)
    source_refs: EvidenceRefs
    status: Literal['source_ambiguous', 'source_conflict', 'unresolved']


class SourceStoryDraft(StrictModel):
    contract_version: StoryVersion
    source_identity: str = Field(min_length=1)
    entities: dict[str, StoryEntity]
    events: dict[str, StoryEvent] = Field(min_length=1)
    propositions: dict[str, StoryProposition]
    knowledge_updates: tuple[StoryKnowledgeUpdate, ...]
    exposures: tuple[StoryExposure, ...]
    state_slots: dict[str, StoryStateSlot]
    state_updates: tuple[StoryStateUpdate, ...]
    causal_links: tuple[StoryCausalLink, ...]
    threads: dict[str, StoryThread]
    world_rules: dict[str, StoryWorldRule]
    expression: StoryExpression
    conflicts: tuple[StoryConflict, ...]
    evidence: dict[str, SourceEvidence] = Field(min_length=1)

    @model_validator(mode='after')
    def _references(self):
        """正式载体自身保护闭集；源字节、语义支持与 owner 归因由编译入口负责。"""
        entities, events, propositions = set(self.entities), set(self.events), set(self.propositions)

        def refs(values, allowed, where):
            bad = set(values) - allowed
            if bad:
                raise ValueError(f'{where} 引用未知主体：{sorted(bad)}')

        refs(self.expression.focalizers, entities, 'expression.focalizers')
        for key, event in self.events.items():
            refs(event.participants, entities, f'events.{key}.participants')
            if len(event.participants) != len(set(event.participants)):
                raise ValueError(f'events.{key} participants 重复')
            if event.temporal.anchor_event:
                refs((event.temporal.anchor_event,), events, f'events.{key}.temporal')
        positions = [(event.chapter_ordinal, event.order) for event in self.events.values()]
        if len(positions) != len(set(positions)):
            raise ValueError('事件阅读位置重复')
        for chapter in {position[0] for position in positions}:
            orders = sorted(position[1] for position in positions if position[0] == chapter)
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError(f'第{chapter}章事件阅读序须从 1 连续')
        for key, prop in self.propositions.items():
            refs(prop.entity_refs, entities, f'propositions.{key}')
        ids = [update.update_id for update in self.knowledge_updates]
        if len(ids) != len(set(ids)):
            raise ValueError('knowledge update_id 重复')
        seen = set()
        ranks = {key: (event.chapter_ordinal, event.order) for key, event in self.events.items()}
        ranks['story_start'] = (0, 0)
        by_knowledge = {u.update_id: u for u in self.knowledge_updates}
        for update in self.knowledge_updates:
            refs((update.holder,), entities, 'knowledge.holder')
            refs((update.proposition_ref,), propositions, 'knowledge.proposition')
            refs((update.at,), events | {'story_start'}, 'knowledge.at')
            refs(update.supersedes, seen, 'knowledge.supersedes')
            for replaced in update.supersedes:
                previous = by_knowledge[replaced]
                if previous.holder != update.holder or ranks[previous.at] > ranks[update.at]:
                    raise ValueError('认知修正必须指向同一持有者在此前的认识')
            seen.add(update.update_id)
        for exposure in self.exposures:
            refs((exposure.event_ref,), events, 'exposure.event')
            refs((exposure.proposition_ref,), propositions, 'exposure.proposition')
            if (exposure.mode == 'confirmed'
                    and self.propositions[exposure.proposition_ref].world_status != 'established'):
                raise ValueError('读者确认不得将已声明 refuted/undetermined 的命题升级成真')
        for slot in self.state_slots.values():
            refs(slot.subjects, entities, 'slot.subjects')
            if len(set(slot.subjects)) != len(slot.subjects):
                raise ValueError('具体状态槽作用对象重复')
        slot_addresses = [(slot.subjects, slot.aspect, slot.focus) for slot in self.state_slots.values()]
        if len(slot_addresses) != len(set(slot_addresses)):
            raise ValueError('同一个具体状态槽不得重复登记为两个 ID')
        state_ids = [u.update_id for u in self.state_updates]
        if len(state_ids) != len(set(state_ids)):
            raise ValueError('state update_id 重复')
        for update in self.state_updates:
            refs((update.slot_ref,), set(self.state_slots), 'state.slot')
            refs((update.at,), events | {'story_start'}, 'state.at')
            if update.proposition_ref:
                refs((update.proposition_ref,), propositions, 'state.proposition')
                prop = self.propositions[update.proposition_ref]
                if (update.modality == 'actual' and update.op == 'set'
                        and prop.world_status != 'established'):
                    raise ValueError('actual 状态不得依据 refuted/undetermined 命题建立')
        for edge in self.causal_links:
            refs((edge.from_event, edge.to_event), events, 'cause')
            if edge.from_event == edge.to_event:
                raise ValueError('因果不得自指')
        for thread in self.threads.values():
            opened, terminal, previous_rank = False, False, (0, 0)
            for update in thread.updates:
                refs((update.event_ref,), events, 'thread.event')
                rank = ranks[update.event_ref]
                if rank < previous_rank:
                    raise ValueError('源线程更新须按阅读序排列')
                if update.op == 'open':
                    if opened:
                        raise ValueError('同一源线程不得重复 open')
                    opened = True
                elif not opened or terminal:
                    raise ValueError('源线程不得孤立收束或在终止后继续更新')
                if update.op in ('resolve', 'abandon'):
                    terminal = True
                previous_rank = rank
        def check_evidence(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ('evidence_refs', 'source_refs') and isinstance(child, (list, tuple)):
                        refs(child, set(self.evidence), key)
                    else:
                        check_evidence(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    check_evidence(child)
        check_evidence(self.model_dump(mode='json'))
        for entity in self.entities.values():
            literals = [surface.literal for surface in entity.surfaces]
            if len(literals) != len(set(literals)):
                raise ValueError('实体称谓字面重复')
            for surface in entity.surfaces:
                if not any(surface.literal in self.evidence[ref].text for ref in surface.evidence_refs):
                    raise ValueError(f'实体称谓 {surface.literal!r} 未出现在自身证据段')
        for event in self.events.values():
            allowed = {event.chapter_ordinal} | ({0} if event.chapter_ordinal == 1 else set())
            if any(self.evidence[ref].chapter_ordinal not in allowed for ref in event.evidence_refs):
                raise ValueError('事件证据不得越出所属源章')
        return self


class SourceStory(SourceStoryDraft):
    """唯一正式载体；未解析姓名的综合草稿只能使用 SourceStoryDraft。"""

    @model_validator(mode='after')
    def _published_names(self):
        if any(conflict.status == 'unresolved' for conflict in self.conflicts):
            raise ValueError('模型未决不能作为已完成的正式事实发布')
        for key, entity in self.entities.items():
            if entity.kind == 'person' and entity.name is None:
                raise ValueError(f'正式人物 {key} 必须具备显式姓名结构')
            if entity.kind != 'person' and entity.name is not None:
                raise ValueError('地点/物件不使用人物姓名结构')
            if entity.name is not None:
                surfaces = {surface.literal for surface in entity.surfaces}
                if set(entity.name.surface_forms) != surfaces:
                    raise ValueError(f'正式人物 {key} 姓名构词未恰好覆盖称谓')
        return self


# 局部观察不假装知道全书身份。label 是语义描述，不是模型生成的机械 ID。
class LocalReferent(StrictModel):
    kind: EntityKind
    label: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    evidence_refs: EvidenceRefs


class LocalKnowledgeHint(StrictModel):
    holder: str
    mode: KnowledgeMode
    polarity: Polarity


class LocalClaim(StrictModel):
    statement: str = Field(min_length=1)
    world_status: WorldStatus
    knowledge: tuple[LocalKnowledgeHint, ...]
    reader_mode: Literal['confirmed', 'claimed', 'suggested', 'none']
    evidence_refs: EvidenceRefs


class LocalThreadHint(StrictModel):
    question_or_resolution: str = Field(min_length=1)
    op: Literal['open', 'progress', 'resolve', 'abandon']
    evidence_refs: EvidenceRefs


class ChapterEventWire(StrictModel):
    summary: str = Field(min_length=1)
    participants: tuple[str, ...]
    temporal: StoryTemporal
    scene_boundary: Literal['new', 'continue', 'unknown']
    evidence_refs: EvidenceRefs
    claims: tuple[LocalClaim, ...]
    causal_hints: tuple[str, ...]
    thread_hints: tuple[LocalThreadHint, ...]


class ContextNeed(StrictModel):
    item: str
    question: str = Field(min_length=1)
    source_refs: tuple[str, ...]


class ChapterWire(StrictModel):
    contract_version: StoryVersion
    referents: dict[str, LocalReferent]
    events: dict[Annotated[str, Field(min_length=1)], ChapterEventWire] = Field(min_length=1)
    context_needs: tuple[ContextNeed, ...]


class ChapterObservation(StrictModel):
    contract_version: StoryVersion
    source_identity: str
    chapter_ordinal: int = Field(ge=1, strict=True)
    referents: dict[str, LocalReferent]
    events: dict[str, ChapterEventWire] = Field(min_length=1)
    context_needs: tuple[ContextNeed, ...]


class EntityWire(StrictModel):
    kind: EntityKind
    label: str = Field(min_length=1)
    surfaces: tuple[StorySurface, ...] = Field(min_length=1)
    mention_refs: tuple[str, ...]


class BookEventWire(StrictModel):
    """键是已给局部事件句柄；事件正文、证据和参与者由 compiler 继承。"""
    temporal: StoryTemporal
    scene_boundary: Literal['new', 'continue', 'unknown']



class SourceObservationIssue(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    item: str = Field(min_length=1)
    path: tuple[str | Annotated[int, Field(strict=True)], ...] = Field(min_length=1)
    operation: Literal['replace', 'remove', 'append']
    kind: Literal['missing', 'contradiction', 'unsupported']
    message: str = Field(min_length=1)
    evidence_refs: EvidenceRefs


SOURCE_REVISION_SELECTORS = ('causal_links', 'exposures', 'conflicts', 'expression')


class SourceRevisionAddress(StrictModel):
    """同一修订在正式事实与真实语义writer中的地址，禁止按证据章节猜归属。"""
    node: str
    owner: str
    item: str
    path: tuple[str | Annotated[int, Field(strict=True)], ...]
    operation: Literal['replace', 'remove', 'append']
    canonical_path: tuple[str | Annotated[int, Field(strict=True)], ...]


class SourceRevisionScopeError(ValueError):
    def __init__(self, code: str, path: tuple, message: str):
        self.code, self.path = code, path
        super().__init__(message)


def source_revision_owner(source: SourceStory, source_ref: str,
                          path: tuple[str | int, ...] | list[str | int],
                          operation: str) -> SourceRevisionAddress:
    """只按正式字段的唯一writer域定位；原wire地址仍须编译器校验映射。

    path相对source_ref那条正式记录。正式事件动作来自chapters，时间/场景来自book；
    派生编号、整条姓名结构和无唯一writer的复合字段不授予隐式整对象修订权限。
    """
    path = tuple(path)

    def fail(code, message):
        raise SourceRevisionScopeError(code, path, message)

    if operation not in ('replace', 'remove', 'append'):
        fail('source_revision_operation', '修订必须显式选择replace/remove/append')
    if any(type(component) not in (str, int) for component in path):
        fail('source_revision_path', '修订路径只接受原字面键与严格整数下标')
    selected = []
    for domain in ('entities', 'events', 'propositions', 'state_slots', 'threads', 'world_rules'):
        if source_ref in getattr(source, domain):
            selected.append((domain, source_ref, getattr(source, domain)[source_ref]))
    for domain in ('knowledge_updates', 'state_updates'):
        selected.extend((domain, number, row) for number, row in enumerate(getattr(source, domain))
                        if row.update_id == source_ref)
    if source_ref in SOURCE_REVISION_SELECTORS:
        selected.append((source_ref, None, getattr(source, source_ref)))
    if len(selected) != 1:
        fail('source_revision_reference', 'source_ref必须唯一指向当前正式事实记录')
    domain, key, row = selected[0]
    current = (source.model_dump(mode='json')[domain] if key is None else row.model_dump(mode='json'))
    for component in path:
        if (isinstance(current, dict) and component in current
                or isinstance(current, list) and type(component) is int and 0 <= component < len(current)):
            current = current[component]
        else:
            fail('source_revision_path', '相对路径必须选择正式记录中的既有字段或追加容器')
    if path and path[0] in ('chapter_ordinal', 'order', 'update_id'):
        fail('source_revision_derived', '章序、阅读序和正式编号由compiler派发，不允许语义修改')
    if key is None:
        if operation == 'remove':
            fail('source_revision_scope', '没有独立行ID的集合不支持删除并压缩数组；须显式扩大scope')
        if operation == 'replace' and not path and domain != 'expression':
            fail('source_revision_scope', 'replace须定位具体既有条目/字段，不能替换整个正式集合')
    owner = 'book'
    if domain == 'events':
        if not path and operation != 'remove':
            fail('source_revision_scope', '整条事件具有多个writer；须明确动作或时间字段')
        if not path or path[0] in ('summary', 'participants', 'evidence_refs'):
            owner = f'ch{row.chapter_ordinal:02d}'
        elif path[0] not in ('temporal', 'scene_boundary'):
            fail('source_revision_scope', '事件字段没有可修订的语义writer')
    elif domain == 'entities' and path and path[0] == 'name':
        if len(path) < 2:
            fail('source_revision_scope', '姓名正式结构含派生字段；须明确原姓名语义部件')
        if path[1] not in ('family_group', 'family', 'given', 'aliases'):
            fail('source_revision_derived', '姓名构词模板由compiler派生，须定位真实姓名部件')
        owner = 'names'
    elif domain == 'entities' and not path and operation == 'replace':
        fail('source_revision_scope', '实体整记录含names生成的姓名结构；须明确book字段')
    if operation == 'remove' and path:
        fail('source_revision_scope', '删除只支持原生语义字典的完整条目，不删除固定字段或压缩列表')
    if operation == 'append' and not (isinstance(current, list)
            or owner == 'names' and path == ('name', 'aliases')):
        fail('source_revision_operation', 'append必须明确既有可追加容器，不能追加固定record字段')
    node = ('str06.stage2.chapters' if owner.startswith('ch') else f'str06.stage2.{owner}')
    canonical_path = (domain, *path) if key is None else (domain, key, *path)
    return SourceRevisionAddress(node=node, owner=owner, item=source_ref, path=path,
                                 operation=operation, canonical_path=canonical_path)


class UnresolvedAssociation(StrictModel):
    key: str = Field(min_length=1)
    kind: Literal['entity_identity', 'proposition_identity', 'thread_identity', 'temporal_anchor']
    left: str
    candidates: tuple[str, ...] = Field(min_length=1)
    question: str = Field(min_length=1)
    evidence_refs: EvidenceRefs


class BookWire(StrictModel):
    contract_version: StoryVersion
    entities: dict[str, EntityWire]
    events: dict[str, BookEventWire] = Field(min_length=1)
    propositions: dict[str, StoryProposition]
    knowledge_updates: dict[str, KnowledgeWire]
    exposures: tuple[StoryExposure, ...]
    state_slots: dict[str, StoryStateSlot]
    state_updates: dict[Annotated[str, Field(min_length=1)], StateUpdateWire]
    causal_links: tuple[StoryCausalLink, ...]
    threads: dict[str, StoryThread]
    world_rules: dict[str, StoryWorldRule]
    expression: StoryExpression
    conflicts: tuple[StoryConflict, ...]
    unresolved: tuple[UnresolvedAssociation, ...]
    source_issues: tuple[SourceObservationIssue, ...]


class BookObservation(StrictModel):
    contract_version: StoryVersion
    story: SourceStoryDraft
    entity_handles: dict[str, str]
    event_handles: dict[str, str]
    proposition_handles: dict[str, str]
    slot_handles: dict[str, str]
    thread_handles: dict[str, str]
    chapter_digests: dict[int, str]
    unresolved: tuple[UnresolvedAssociation, ...]


class BookRepairScope(StrictModel):
    contract_version: StoryVersion
    source_identity: str
    content_sha256: str
    baseline_sha256: str
    before_digests: dict[int, str]
    after_digests: dict[int, str]
    changed_chapters: tuple[int, ...]
    affected_evidence: tuple[str, ...]
    affected_references: dict[str, tuple[str, ...]]
    allowed_paths: tuple[tuple[str | int, ...], ...]


class BookRepairChange(StrictModel):
    path: tuple[str | int, ...] = Field(min_length=1)
    operation: Literal['set', 'remove']
    value: Any = None
    reason: str = Field(min_length=1)
    evidence_refs: EvidenceRefs

    @model_validator(mode='after')
    def _operation(self):
        if self.operation == 'remove' and self.value is not None:
            raise ValueError('remove不得携带替代值')
        if self.operation == 'set' and 'value' not in self.model_fields_set:
            raise ValueError('set必须显式提供value')
        return self


class BookRepairWire(StrictModel):
    contract_version: StoryVersion
    kind: Literal['book_repair']
    changes: tuple[BookRepairChange, ...] = Field(min_length=1)


class PersonNameWire(StrictModel):
    family_group: str | None
    family: str | None
    given: str | None
    aliases: tuple[str, ...]
    # 哪些字面只是称谓属于语义；构词模板由 compiler 根据已判定的部件派生。
    pure_titles: tuple[str, ...]


class NamesWire(StrictModel):
    contract_version: StoryVersion
    people: dict[str, PersonNameWire]


class NamesObservation(StrictModel):
    contract_version: StoryVersion
    people: dict[str, StoryNameStructure]


class AssociationVerdict(StrictModel):
    kind: Literal['identity']
    key: str
    verdict: Literal['same', 'distinct', 'source_unknown', 'unresolved']
    right: str | None = None
    reason: str = Field(min_length=1)
    evidence_refs: EvidenceRefs

    @model_validator(mode='after')
    def _answer(self):
        if (self.verdict == 'same') != bool(self.right):
            raise ValueError('same 必须选择一个 right；其他判定不得带 right')
        return self


class TemporalAnchorVerdict(StrictModel):
    kind: Literal['temporal_anchor']
    key: str
    verdict: Literal['resolved', 'source_unknown', 'unresolved']
    frame: TemporalFrame
    anchor_event: str | None
    reason: str = Field(min_length=1)
    evidence_refs: EvidenceRefs

    @model_validator(mode='after')
    def _answer(self):
        if self.verdict == 'resolved':
            if self.frame == 'unknown' or self.anchor_event is None:
                raise ValueError('时间判定resolved必须明写已知frame及候选anchor_event')
        elif self.frame != 'unknown' or self.anchor_event is not None:
            raise ValueError('时间未知或未决不得暗中选择frame或anchor_event')
        return self


class AssociateObservation(StrictModel):
    contract_version: StoryVersion
    verdicts: tuple[Annotated[AssociationVerdict | TemporalAnchorVerdict,
                              Field(discriminator='kind')], ...]


__all__ = [name for name in globals() if name.startswith(('Story', 'Source', 'Chapter', 'Book',
            'Local', 'Names', 'Person', 'Associate', 'Association', 'Unresolved', 'Context', 'Temporal'))]
