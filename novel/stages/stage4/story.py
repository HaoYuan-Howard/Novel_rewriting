"""story-v1 的纯目标建模边界：提示、确定性编译、验证与写手投影。

不读文件、不调用模型、不修输入。源审计引用只留 PreservationContract 与
AdaptationMap；目标模型、章计划和写手材料不依赖源章/拍拓扑。
"""
from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, Iterable, Mapping, TypeVar

from contracts.schema.base import StrictModel
from contracts.schema.source_story import (
    SOURCE_REVISION_SELECTORS, StoryIssue, SourceRevisionScopeError, source_revision_owner,
)
from contracts.schema.target_story import (
    AdaptationEntry, AdaptationMap, CharacterArc, ChapterPacket, ChapterPlan,
    ChapterPlanWire, HookBeat, HookDesignWire, HookPacket, HookPlan, KnowledgeWire,
    NameMap, NamingWire, PreservationContract, PreservationRequirement,
    PreserveWire, StoryState, TargetChapter, TargetEvent, TargetKnowledge,
    TargetModelDraft, TargetModelWire, TargetName, TargetStory, TargetWorldRule, writer_context_issues,
)

M = TypeVar('M', bound=StrictModel)


class TargetStoryIssue(ValueError):
    """确定的语义owner停点；调用方应回派，不能退成当前owner的格式重试。"""

    def __init__(self, message: str, *, owner: str, node: str, item: str,
                 code: str, context: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.owner = owner
        self.node = node
        self.item = item
        self.code = code
        self.context = dict(context or {})

    def as_issue(self) -> dict[str, Any]:
        return {'owner': self.owner, 'node': self.node, 'item': self.item,
                'code': self.code, 'message': str(self), 'context': self.context}


class TargetCompileError(ValueError):
    """当前owner可修的明确字段问题，与跨owner回派分别表达。"""

    def __init__(self, issues: Iterable[StoryIssue]):
        self.issues = tuple(issues)
        super().__init__('；'.join(issue.message for issue in self.issues))


def _fail(message: str, path: tuple, *, code: str = 'target_contract') -> None:
    raise TargetCompileError((StoryIssue(code=code, owner='current', item='book',
                                         path=path, message=message),))


def _wire(cls: type[M], value: Mapping[str, Any] | M) -> M:
    # 即使调用方给了 typed 实例也重验；model_copy 不保证 validate。
    return cls.model_validate(value.model_dump(mode='json')
                              if isinstance(value, StrictModel) else value)


def _unique(values: Iterable[Any], where: str, *, path: tuple = ()) -> tuple[Any, ...]:
    result = tuple(values)
    if len(result) != len(set(result)):
        _fail(f'{where} 含重复引用', path, code='duplicate_reference')
    return result


def _closed(values: Iterable[str], known: Iterable[str], where: str, *, path: tuple = ()) -> None:
    unknown = set(values) - set(known)
    if unknown:
        _fail(f'{where} 引用闭集外主体：{sorted(unknown)}', path, code='unknown_reference')


def _refs(numbers: Iterable[int], ids: tuple[str, ...], where: str, *, path: tuple = ()) -> tuple[str, ...]:
    values = _unique(numbers, where, path=path)
    if any(isinstance(n, bool) or n < 1 or n > len(ids) for n in values):
        _fail(f'{where} 序号越界，合法范围 1..{len(ids)}', path, code='reference_out_of_range')
    return tuple(ids[n - 1] for n in values)


def _acyclic(edges: Mapping[str, Iterable[str]], where: str, *, path: tuple = (),
             item_paths: Mapping[str, tuple] | None = None) -> None:
    pending = {node: set(deps) for node, deps in edges.items()}
    while pending:
        ready = {node for node, deps in pending.items() if not deps}
        if not ready:
            message = f'{where} 含环：{sorted(pending)}'
            raise TargetCompileError(StoryIssue(code='reference_cycle', owner='current', item='book',
                path=(item_paths.get(node, path) if item_paths else path), message=message)
                for node in (sorted(pending) if item_paths else ('graph',)))
        pending = {node: deps - ready for node, deps in pending.items() if node not in ready}


def _format_name(template: str, *, family: str | None, given: str | None,
                 aliases: Mapping[str, str]) -> str:
    text = template.replace('{family}', family or '').replace('{given}', given or '')
    for key, literal in aliases.items():
        text = text.replace('{alias:' + key + '}', literal)
    if '{' in text or '}' in text or not text.strip():
        raise ValueError(f'名称模板不能闭合：{template!r}')
    return text


def compile_naming(wire: Mapping[str, Any] | NamingWire, source) -> NameMap:
    """选择目标名称，构词及派生称谓由 compiler 生成。"""
    data = _wire(NamingWire, wire)
    people = {ref: entity for ref, entity in source.entities.items() if entity.kind == 'person'}
    selected = {entry.entity_ref: entry for entry in data.entries}
    if len(selected) != len(data.entries) or set(selected) != set(people):
        _fail('Naming entries 必须恰好覆盖全部 person，不能重复、漏项或增项', ('entries',))
    names: dict[str, TargetName] = {}
    family_groups: dict[str, str] = {}
    target_families: dict[str, str] = {}
    for ref, entity in source.entities.items():
        if entity.kind != 'person':
            names[ref] = TargetName(entity_id=ref, primary=entity.label,
                                    surfaces={s.literal: s.literal for s in entity.surfaces})
            continue
        entry = selected[ref]
        path = ('entries', next(i for i, row in enumerate(data.entries) if row.entity_ref == ref))
        structure = entity.name
        if structure is None:
            raise TargetStoryIssue(f'Naming {ref} 缺源名称结构，交回 Stage2 names',
                owner='names', node='str06.stage2.names', item=ref, code='source_name_structure_missing')
        if entry.action == 'keep':
            if not entry.reason.strip():
                _fail(f'Naming {ref} keep 必须说明理由', (*path, 'reason'))
            if entry.family is not None or entry.given is not None or entry.aliases:
                _fail(f'Naming {ref} keep 不得混入改名字段', path)
            if any(form is not None for form in structure.surface_forms.values()):
                _fail(f'Naming {ref} 有正式姓名位，必须 rename', path)
            primary = entity.label
            surfaces = {s.literal: s.literal for s in entity.surfaces}
        else:
            if bool(structure.family) != bool(entry.family) or bool(structure.given) != bool(entry.given):
                _fail(f'Naming {ref} 必须保留已有姓/名结构位', path)
            if set(entry.aliases) != set(structure.aliases):
                _fail(f'Naming {ref} 化名位不完整', (*path, 'aliases'))
            if any(not literal.strip() for literal in entry.aliases.values()):
                _fail(f'Naming {ref} 化名字面不得为空', (*path, 'aliases'))
            literals = [s.literal for s in entity.surfaces]
            old_parts = {p for p in (structure.family, structure.given,
                                      *structure.aliases.values()) if p}
            new_parts = {p for p in (entry.family, entry.given, *entry.aliases.values()) if p}
            if new_parts & old_parts:
                _fail(f'Naming {ref} 新姓名部件仍含源旧字面', path)
            surfaces = {literal: (_format_name(form, family=entry.family, given=entry.given,
                                              aliases=entry.aliases) if form is not None else literal)
                        for literal, form in structure.surface_forms.items()}
            if set(literals) - set(surfaces):
                raise TargetStoryIssue(f'Naming {ref} 源称谓缺少构词登记', owner='names',
                    node='str06.stage2.names', item=ref, code='source_name_form_missing')
            primary = ((entry.family or '') + (entry.given or '')
                       or next(iter(entry.aliases.values()), ''))
            if not primary:
                _fail(f'Naming {ref} 没有可用目标称呼', path)
            if structure.family_group and entry.family:
                group = structure.family_group
                if group in family_groups and family_groups[group] != entry.family:
                    _fail(f'Naming 同家族 {group} 的目标姓不一致', (*path, 'family'))
                if entry.family in target_families and target_families[entry.family] != group:
                    _fail('Naming 不同源家族不得合并为同一目标姓', (*path, 'family'))
                family_groups[group] = entry.family
                target_families[entry.family] = group
        names[ref] = TargetName(entity_id=ref, primary=primary, surfaces=surfaces)
    primary_counts = Counter(name.primary for ref, name in names.items() if ref in people)
    if any(n > 1 for n in primary_counts.values()):
        raise TargetCompileError(StoryIssue(code='target_name_collision', owner='current', item='book',
            path=('entries', i), message='Naming 目标人物主称谓冲突')
            for i, entry in enumerate(data.entries) if primary_counts[names[entry.entity_ref].primary] > 1)
    return NameMap(contract_version='story-v1', names=names)


def _source_refs(source) -> set[str]:
    result: set[str] = set()
    for field in ('entities', 'events', 'propositions', 'state_slots', 'threads', 'world_rules'):
        result.update(getattr(source, field))
    result.update(update.update_id for update in source.state_updates)
    result.update(update.update_id for update in source.knowledge_updates)
    return result


def compile_preserve(wire: Mapping[str, Any] | PreserveWire, source,
                     name_map: NameMap) -> PreservationContract:
    data = _wire(PreserveWire, wire)
    if data.source_issues:
        _raise_source_issues(data, source)
    _closed(name_map.names, source.entities, 'Preserve 名称表', path=('requirements',))
    ids = tuple(f'rq{n:04d}' for n in range(1, len(data.requirements) + 1))
    requirements: list[PreservationRequirement] = []
    bindings: dict[str, tuple[str, ...]] = {}
    for index, (ref, item) in enumerate(zip(ids, data.requirements)):
        path = ('requirements', index)
        _closed(item.source_refs, _source_refs(source), f'Preserve {ref}.source_refs', path=(*path, 'source_refs'))
        _closed(item.participants, source.entities, f'Preserve {ref}.participants', path=(*path, 'participants'))
        requires = _refs(item.requires, ids, f'Preserve {ref}.requires', path=(*path, 'requires'))
        if ref in requires:
            _fail(f'Preserve {ref} 不得依赖自身', (*path, 'requires'))
        bindings[ref] = _unique(item.source_refs, f'Preserve {ref}.source_refs', path=(*path, 'source_refs'))
        requirements.append(PreservationRequirement(
            requirement_id=ref, kind=item.kind, statement=item.statement,
            participants=_unique(item.participants, f'Preserve {ref}.participants', path=(*path, 'participants')),
            required=item.required, satisfaction=item.satisfaction, requires=requires,
            constraints=item.constraints, details=item.details))
    _acyclic({r.requirement_id: r.requires for r in requirements}, 'Preserve requires',
        item_paths={ref: ('requirements', index, 'requires') for index, ref in enumerate(ids)})
    decisions = {d.rule_ref: d for d in data.world_rules}
    if len(decisions) != len(data.world_rules) or set(decisions) != set(source.world_rules):
        _fail('Preserve 世界规则须逐项恰好决定 keep/adapt/omit', ('world_rules',))
    rules = []
    for source_ref in source.world_rules:
        decision = decisions[source_ref]
        if decision.action == 'omit':
            if any(r.required and source_ref in bindings[r.requirement_id] for r in requirements):
                _fail(f'Preserve 必保规则 {source_ref} 不得 omit，需 adapt',
                    ('world_rules', next(i for i, d in enumerate(data.world_rules) if d.rule_ref == source_ref)))
            continue
        rules.append(TargetWorldRule(rule_id=f'wr{len(rules) + 1:04d}',
                                     statement=decision.target_statement))
    conflict_nos = _unique((c.conflict_no for c in data.consistency_decisions), 'Preserve conflicts', path=('consistency_decisions',))
    if set(conflict_nos) != set(range(1, len(source.conflicts) + 1)):
        _fail('Preserve 每条源冲突都须显式保留含混或局部一致化', ('consistency_decisions',))
    return PreservationContract(contract_version='story-v1', requirements=tuple(requirements), world_rules=tuple(rules),
        world_rule_decisions=data.world_rules,
        consistency_decisions=data.consistency_decisions, rewrite_direction=data.rewrite_direction,
        expression=data.expression, source_bindings=bindings)


def _raise_source_issues(data: PreserveWire, source) -> None:
    routed = []
    for index, issue in enumerate(data.source_issues):
        _closed((issue.source_ref,), _source_refs(source) | set(SOURCE_REVISION_SELECTORS), 'Preserve source_issues.source_ref',
                path=('source_issues', index, 'source_ref'))
        _closed(issue.evidence_refs, source.evidence, 'Preserve source_issues.evidence_refs',
                path=('source_issues', index, 'evidence_refs'))
        try:
            address = source_revision_owner(source, issue.source_ref, issue.path, issue.operation)
        except SourceRevisionScopeError as exc:
            _fail(str(exc), ('source_issues', index, 'path'), code=exc.code)
        routed.append({'owner': address.owner, 'node': address.node, 'item': address.item,
            'code': 'source_fact_' + issue.kind,
            'message': issue.statement, 'context': {'source_ref': issue.source_ref,
                'path': list(issue.path), 'operation': issue.operation,
                'canonical_path': list(address.canonical_path),
                                                  'evidence_refs': list(issue.evidence_refs)}})
    first = routed[0]
    raise TargetStoryIssue(first['message'], owner=first['owner'], node=first['node'],
        item=first['item'], code=first['code'], context={'owner_issues': routed})


def _state_address(state: StoryState) -> tuple[tuple[str, ...], str, str, str]:
    return state.subject_ids, state.dimension, state.qualifier, state.modality


def _check_states(states: Iterable[StoryState], entities: Iterable[str], where: str,
                  *, path: tuple) -> None:
    states = tuple(states)
    _unique((_state_address(s) for s in states), where, path=path)
    for index, state in enumerate(states):
        _unique(state.subject_ids, where + '.subject_ids', path=(*path, index, 'subject_ids'))
        _closed(state.subject_ids, entities, where, path=(*path, index, 'subject_ids'))


def _knowledge(rows: Iterable[KnowledgeWire], prop_ids: tuple[str, ...],
               entities: Iterable[str], where: str, *, path: tuple) -> tuple[TargetKnowledge, ...]:
    result = []
    for index, row in enumerate(rows):
        _closed((row.holder,), (*entities, 'reader'), where + '.holder', path=(*path, index, 'holder'))
        prop = _refs((row.proposition_no,), prop_ids, where + '.proposition_no',
                     path=(*path, index, 'proposition_no'))[0]
        result.append(TargetKnowledge(holder=row.holder, proposition_ref=prop, mode=row.mode))
    _unique(((k.holder, k.proposition_ref) for k in result), where, path=path)
    return tuple(result)


def _budgets(total: int, weights: tuple[float, ...]) -> tuple[int, ...]:
    if total < len(weights):
        raise ValueError('全书篇幅预算小于章节数')
    remaining = total - len(weights)
    denominator = math.fsum(weights)
    shares = [remaining * w / denominator for w in weights]
    allocated = [1 + math.floor(s) for s in shares]
    order = sorted(range(len(weights)), key=lambda i: (-(shares[i] - math.floor(shares[i])), i))
    for i in order[:total - sum(allocated)]:
        allocated[i] += 1
    return tuple(allocated)


def compile_target_model(wire: Mapping[str, Any] | TargetModelWire,
                         preservation: PreservationContract, *, source_chapter_count: int,
                         total_length_budget: int, name_map: NameMap) -> TargetModelDraft:
    data = _wire(TargetModelWire, wire)
    if source_chapter_count < 1 or len(data.chapters) != source_chapter_count:
        _fail('目标章节数必须等于源 chapter_ordinal 数量', ('chapters',))
    entities = tuple(name_map.names)
    if set(data.initial_displays) != set(entities):
        _fail('TargetModel initial_displays须覆盖全部实体；隐藏身份使用已获准的纯称谓', ('initial_displays',))
    for ref, display in data.initial_displays.items():
        _check_display(ref, display, name_map, path=('initial_displays', ref))
    _closed(data.character_voices, entities, 'TargetModel character_voices', path=('character_voices',))
    _check_states(data.initial_state, entities, 'TargetModel initial_state', path=('initial_state',))
    for index, state in enumerate(data.initial_state):
        if state.op == 'unset':
            _fail('初始状态不能解除尚未建立的状态槽', ('initial_state', index))
    event_ids = tuple(f'ev{n:04d}' for n in range(1, len(data.events) + 1))
    prop_ids = tuple(f'tp{n:04d}' for n in range(1, len(data.propositions) + 1))
    propositions = dict(zip(prop_ids, data.propositions))
    initial_knowledge = _knowledge(data.initial_knowledge, prop_ids, entities, 'initial_knowledge', path=('initial_knowledge',))
    requirement_ids = tuple(r.requirement_id for r in preservation.requirements)
    events = []
    for index, (ref, event) in enumerate(zip(event_ids, data.events)):
        path = ('events', index)
        _closed(event.participants, entities, f'{ref}.participants', path=(*path, 'participants'))
        _unique(event.participants, f'{ref}.participants', path=(*path, 'participants'))
        _closed(event.realizes, requirement_ids, f'{ref}.realizes', path=(*path, 'realizes'))
        _unique(event.realizes, f'{ref}.realizes', path=(*path, 'realizes'))
        _check_states(event.state_changes, entities, f'{ref}.state_changes', path=(*path, 'state_changes'))
        _closed(event.name_releases, event.participants, f'{ref}.name_releases', path=(*path, 'name_releases'))
        for person, display in event.name_releases.items():
            _check_display(person, display, name_map, path=(*path, 'name_releases', person))
        events.append(TargetEvent(event_id=ref, summary=event.summary,
            participants=event.participants, trigger=event.trigger, choice=event.choice,
            result=event.result, requires=_refs(event.requires, event_ids, f'{ref}.requires', path=(*path, 'requires')),
            after=_refs(event.after, event_ids, f'{ref}.after', path=(*path, 'after')), temporal_frame=event.temporal_frame,
            realizes=event.realizes, state_changes=event.state_changes,
            knowledge_reads=_knowledge(event.knowledge_reads, prop_ids, entities, f'{ref}.knowledge_reads', path=(*path, 'knowledge_reads')),
            knowledge_changes=_knowledge(event.knowledge_changes, prop_ids, entities, f'{ref}.knowledge_changes', path=(*path, 'knowledge_changes')),
            name_releases=dict(event.name_releases)))
    paths = {ref: ('events', index) for index, ref in enumerate(event_ids)}
    _acyclic({event.event_id: event.requires for event in events}, '目标因果', item_paths=paths)
    _acyclic({event.event_id: event.after for event in events}, '目标时间', item_paths=paths)
    _acyclic({event.event_id: (*event.requires, *event.after) for event in events}, '目标因果/时间联合图', item_paths=paths)
    assignments = [_refs(ch.event_nos, event_ids, f'第 {i} 章 event_nos', path=('chapters', i - 1, 'event_nos'))
                   for i, ch in enumerate(data.chapters, 1)]
    flattened = tuple(ref for chapter in assignments for ref in chapter)
    if Counter(flattened) != Counter(event_ids):
        _fail('目标事件必须逐个且仅归属一个章节，不能漏项、重复或越界', ('chapters',))
    events_of = {event.event_id: event for event in events}
    positions = {ref: i for i, ref in enumerate(flattened)}
    for event in events:
        for parent in event.requires:
            if positions[parent] >= positions[event.event_id] and event.temporal_frame != 'past':
                _fail(f'{event.event_id} 的必要因果前提 {parent} 尚未发生',
                    (*paths[event.event_id], 'requires'))
    arcs = []
    for index, arc in enumerate(data.character_arcs):
        _closed((arc.entity_ref,), entities, '人物弧', path=('character_arcs', index, 'entity_ref'))
        refs = _refs(arc.event_nos, event_ids, f'人物弧 {arc.entity_ref}', path=('character_arcs', index, 'event_nos'))
        if not any(arc.entity_ref in events_of[ref].participants for ref in refs):
            _fail(f'人物弧 {arc.entity_ref} 未参与任何转变事件', ('character_arcs', index))
        arcs.append(CharacterArc(entity_ref=arc.entity_ref, starting_position=arc.starting_position,
            pressure=arc.pressure, choice=arc.choice, event_refs=refs, end_position=arc.end_position))
    budgets = _budgets(total_length_budget, tuple(ch.weight for ch in data.chapters))
    state = {_state_address(s): s for s in data.initial_state}
    knowledge = {(k.holder, k.proposition_ref): k for k in initial_knowledge}
    displays = dict(data.initial_displays)
    chapters = []
    for ordinal, (chapter, refs, budget) in enumerate(zip(data.chapters, assignments, budgets), 1):
        entry_state, entry_knowledge = tuple(state.values()), tuple(knowledge.values())
        entry_displays = dict(displays)
        for ref in refs:
            event = events_of[ref]
            for read in event.knowledge_reads:
                if event.temporal_frame != 'current' and read.holder != 'reader':
                    continue
                available = knowledge.get((read.holder, read.proposition_ref))
                if available is None or available.mode != read.mode:
                    _fail(f'{ref} 知情读取缺少同模式前提：{read.holder}/{read.proposition_ref}',
                        (*paths[ref], 'knowledge_reads'))
            # 插叙/预叙不是当前世界状态转移；不能用它倒写当前人物状态。
            if event.temporal_frame == 'current':
                for change_index, change in enumerate(event.state_changes):
                    address = _state_address(change)
                    if change.op == 'unset':
                        if address not in state:
                            _fail(f'{ref} 不能解除不存在的状态槽',
                                (*paths[ref], 'state_changes', change_index))
                        del state[address]
                    else:
                        state[address] = change
            knowledge.update({(k.holder, k.proposition_ref): k for k in event.knowledge_changes
                              if event.temporal_frame == 'current' or k.holder == 'reader'})
            displays.update(event.name_releases)
            for change in event.knowledge_changes:
                if change.mode == 'known' and propositions[change.proposition_ref].status != 'established':
                    _fail(f'{ref} 不能把未确立/已推翻命题当作确知', (*paths[ref], 'knowledge_changes'))
        participants = tuple(dict.fromkeys(p for ref in refs for p in events_of[ref].participants))
        chapters.append(TargetChapter(chapter_ordinal=ordinal, purpose=chapter.purpose,
            event_refs=refs, entry=chapter.entry, exit=chapter.exit, length_budget=budget,
            participants=participants, entry_state=entry_state, exit_state=tuple(state.values()),
            entry_knowledge=entry_knowledge, exit_knowledge=tuple(knowledge.values()),
            entry_displays=entry_displays, exit_displays=dict(displays)))
    for index, known in enumerate(initial_knowledge):
        if known.mode == 'known' and propositions[known.proposition_ref].status != 'established':
            _fail('初始知情不能把未确立/已推翻命题当作确知', ('initial_knowledge', index))
    return TargetModelDraft(contract_version='story-v1', source_chapter_count=source_chapter_count,
        total_length_budget=total_length_budget, premise=data.premise, narration=data.narration,
        style=data.style, character_voices=dict(data.character_voices), character_arcs=tuple(arcs),
        world_rules=preservation.world_rules, propositions=propositions,
        initial_state=data.initial_state, initial_knowledge=initial_knowledge,
        initial_displays=dict(data.initial_displays),
        events=tuple(events), chapters=tuple(chapters))


def _check_display(entity_ref: str, display: str, name_map: NameMap, *, path: tuple) -> None:
    for other, name in name_map.names.items():
        if other != entity_ref and display == name.primary:
            _fail(f'{entity_ref} 的显示称谓冒充了另一实体 {other}', path)
    # 登记称谓或纯描述均可；不按字面猜新身份/别名同一性。
    if not display.strip():
        _fail(f'{entity_ref} 的显示称谓为空', path)


def _chapter(model: TargetModelDraft, ordinal: int) -> TargetChapter:
    if isinstance(ordinal, bool) or not 1 <= ordinal <= model.source_chapter_count:
        raise ValueError(f'chapter_ordinal 越界：{ordinal}')
    return model.chapters[ordinal - 1]


def compile_chapter(wire: Mapping[str, Any] | ChapterPlanWire, model: TargetModelDraft,
                    *, chapter_ordinal: int) -> ChapterPlan:
    data = _wire(ChapterPlanWire, wire)
    chapter = _chapter(model, chapter_ordinal)
    if data.model_issues:
        items = []
        for index, issue in enumerate(data.model_issues):
            if issue.event_no is not None:
                item = _refs((issue.event_no,), chapter.event_refs, '章节model_issues',
                    path=('model_issues', index, 'event_no'))[0]
                event_no = next(n for n, event in enumerate(model.events) if event.event_id == item)
                path = ('events', event_no, issue.component)
                payload = getattr(model.events[event_no], issue.component)
            elif issue.component in ('chapter_entry', 'chapter_exit'):
                item = f'chapter:{chapter_ordinal}'
                path = ('chapters', chapter_ordinal - 1, issue.component.removeprefix('chapter_'))
                payload = getattr(chapter, issue.component.removeprefix('chapter_'))
            else:
                item, path = issue.component, (issue.component,)
                payload = getattr(model, issue.component)
            if issue.item_no is not None:
                if issue.item_no > len(payload):
                    _fail('model_issues只能replace既有条目；新增须显式append', ('model_issues', index, 'item_no'))
                path = (*path, issue.item_no - 1)
            context = {'paths': [list(path)] if issue.operation == 'replace' else [],
                'append_paths': [list(path)] if issue.operation == 'append' else [],
                'component': issue.component, 'operation': issue.operation, 'item_no': issue.item_no,
                'validation_identity': f'{item}:{issue.component}:{issue.operation}:{issue.item_no}'}
            items.append({'owner': 'body_design', 'node': 'str06.stage4.body_design',
                'item': item, 'code': 'target_model_' + issue.kind, 'message': issue.statement, 'context': context})
        raise TargetStoryIssue(items[0]['message'], owner='body_design',
            node='str06.stage4.body_design', item=items[0]['item'], code=items[0]['code'],
            context={'owner_issues': items, 'chapter_ordinal': chapter_ordinal})
    if data.cross_boundary_changes:
        raise TargetStoryIssue(f'第 {chapter_ordinal} 章要求新增跨章事实，须窄修 TargetModel',
            owner='body_design', node='str06.stage4.body_design', item=f'chapter:{chapter_ordinal}',
            code='target_cross_boundary_change',
            context={'requested_changes': [s.model_dump(mode='json') for s in data.cross_boundary_changes]})
    referenced = []
    for i, scene in enumerate(data.scenes, 1):
        path = ('scenes', i - 1)
        _closed(scene.participants, chapter.participants, f'第 {chapter_ordinal} 章场 {i}', path=(*path, 'participants'))
        _unique(scene.participants, f'第 {chapter_ordinal} 章场 {i}.participants', path=(*path, 'participants'))
        _closed((scene.pov,), (*chapter.participants, 'narrator'), f'第 {chapter_ordinal} 章场 {i}.pov', path=(*path, 'pov'))
        for index, action in enumerate(scene.actions):
            if action.event_no is not None:
                referenced.extend(_refs((action.event_no,), chapter.event_refs, f'第 {chapter_ordinal} 章行动',
                    path=(*path, 'actions', index, 'event_no')))
    if Counter(referenced) != Counter(chapter.event_refs):
        _fail(f'第 {chapter_ordinal} 章全部目标事件须恰好由一个完整行动承载', ('scenes',))
    if tuple(referenced) != chapter.event_refs:
        _fail(f'第 {chapter_ordinal} 章不能悄改已冻结的事件顺序', ('scenes',))
    problems = writer_context_issues(model, chapter, data.writer_context)
    if problems:
        raise TargetCompileError(StoryIssue(code='writer_context_reference', owner='current',
            item=f'chapter:{chapter_ordinal}', path=path, message=message) for path, message in problems)
    return ChapterPlan(contract_version='story-v1', chapter_ordinal=chapter_ordinal, scenes=data.scenes,
                       writer_context=data.writer_context)


def compile_target_story(model: TargetModelDraft, plans: Iterable[ChapterPlan],
                         preservation: PreservationContract, name_map: NameMap
                         ) -> tuple[TargetStory, AdaptationMap]:
    plans = tuple(sorted(plans, key=lambda p: p.chapter_ordinal))
    if tuple(p.chapter_ordinal for p in plans) != tuple(range(1, model.source_chapter_count + 1)):
        raise ValueError('ChapterPlan 集合必须恰好覆盖全部章节')
    realized = {req.requirement_id: tuple(e.event_id for e in model.events
                    if req.requirement_id in e.realizes) for req in preservation.requirements}
    for requirement in preservation.requirements:
        if requirement.required and not realized[requirement.requirement_id]:
            raise TargetStoryIssue(f'必保义务 {requirement.requirement_id} 没有目标承载',
                owner='body_design', node='str06.stage4.body_design', item=requirement.requirement_id,
                code='required_preservation_missing', context={'requirement_id': requirement.requirement_id})
    position = {ref: index for index, ref in enumerate(
        ref for chapter in model.chapters for ref in chapter.event_refs)}
    for requirement in preservation.requirements:
        if not realized[requirement.requirement_id]:
            continue
        accepted = max(position[ref] for ref in realized[requirement.requirement_id])
        for predecessor in requirement.requires:
            if not realized[predecessor] or max(position[ref] for ref in realized[predecessor]) > accepted:
                raise TargetStoryIssue(f'义务 {requirement.requirement_id} 的前提 {predecessor} 未先兑现',
                    owner='body_design', node='str06.stage4.body_design', item=requirement.requirement_id,
                    code='preservation_order', context={'requires': predecessor})
    for plan in plans:
        compile_chapter(ChapterPlanWire(protocol_version='story-chapter-v1', scenes=plan.scenes,
                                       writer_context=plan.writer_context,
                                       cross_boundary_changes=(), model_issues=()),
                        model, chapter_ordinal=plan.chapter_ordinal)
    story = TargetStory(**model.model_dump(mode='json'), names=name_map.names,
        chapter_plans=plans, preservation=preservation.requirements)
    adaptation = AdaptationMap(contract_version='story-v1', entries=tuple(AdaptationEntry(requirement_id=r.requirement_id,
        source_refs=preservation.source_bindings[r.requirement_id],
        target_event_refs=realized[r.requirement_id],
        status='realized' if realized[r.requirement_id] else 'optional_omitted')
        for r in preservation.requirements), consistency_decisions=preservation.consistency_decisions,
        world_rule_decisions=preservation.world_rule_decisions)
    return story, adaptation


def _band(budget: int, length_band: tuple[float, float]) -> tuple[int, int]:
    low, high = length_band
    if not (math.isfinite(low) and math.isfinite(high) and 0 < low <= 1 <= high):
        raise ValueError('length_band 须为有限正数且包含 1')
    return max(1, math.ceil(budget * low)), max(1, math.floor(budget * high))


def _state_cues(cues, snapshot, names):
    modes = {'actual': '实际', 'planned': '计划', 'expected': '预期',
             'conditional': '条件', 'unknown': '模态未知'}
    operations = {'set': '状态约束', 'unset': '明确解除', 'unknown': '状态值未知'}
    return tuple(('、'.join(names[ref] for ref in snapshot[cue.item_no - 1].subject_ids) or '世界')
        + f'〔{modes[snapshot[cue.item_no - 1].modality]}；{operations[snapshot[cue.item_no - 1].op]}〕：'
        + cue.text for cue in cues)


def _knowledge_cues(cues, snapshot, names):
    modes = {'known': '已确认', 'believed': '相信', 'suspected': '怀疑',
             'heard': '听闻', 'refuted': '已否定'}
    return tuple(('读者' if snapshot[cue.item_no - 1].holder == 'reader'
        else names[snapshot[cue.item_no - 1].holder])
        + modes[snapshot[cue.item_no - 1].mode] + '：' + cue.text for cue in cues)


def project_chapter_packet(story: TargetStory, chapter_ordinal: int, *,
                           length_band: tuple[float, float] = (0.8, 1.2)) -> ChapterPacket:
    chapter = _chapter(story, chapter_ordinal)
    plan = story.chapter_plans[chapter_ordinal - 1]
    context = plan.writer_context
    names = {ref: chapter.exit_displays[ref] for ref in chapter.participants}
    entry_state = _state_cues(context.entry_state, chapter.entry_state, chapter.entry_displays)
    exit_state = _state_cues(context.exit_state, chapter.exit_state, chapter.exit_displays)
    events = {event.event_id: event for event in story.events}
    displays = dict(chapter.entry_displays)
    actions, direction = [], []
    for scene_no, scene in enumerate(plan.scenes, 1):
        pov = displays.get(scene.pov, '叙述者')
        direction.append(f'第{scene_no}场：{scene.setting}；时间：{scene.time}；视角：{pov}；'
            f'诉求：{scene.goal}；阻力：{scene.opposition}；转折：{scene.turn}；演出：{scene.realization}')
        for action in scene.actions:
            temporal = ''
            if action.event_no is not None:
                event = events[chapter.event_refs[action.event_no - 1]]
                temporal = {'current': '当前发生', 'past': '过去回溯', 'future': '未来预叙',
                            'summary': '概括性叙述', 'unknown': '时间未定'}[event.temporal_frame]
                displays.update(event.name_releases)
            actions.append(f'故事时间：{temporal or "承接当下"}；触发：{action.trigger}；选择：{action.choice}；'
                f'行动：{action.action}；回应：{action.response}；结果：{action.result}；衔接：{action.transition}')
            if action.event_no is not None:
                actions.extend(f'称谓在此处正式揭示为：{display}' for display in event.name_releases.values())
    low, high = _band(chapter.length_budget, length_band)
    return ChapterPacket(contract_version='story-v1', chapter_ordinal=chapter_ordinal,
        source_chapter_count=story.source_chapter_count, length_budget=chapter.length_budget,
        min_chars=low, max_chars=high, participants=chapter.participants, names=names,
        entry_state=entry_state, exit_state=exit_state, actions=tuple(actions),
        must_realize=tuple(actions),
        must_respect=(f'叙述：{context.narration}', *(cue.text for cue in context.world_rules),
            *_knowledge_cues(context.entry_knowledge, chapter.entry_knowledge, chapter.entry_displays),
            *(cue.text for cue in context.requirements if cue.placement == 'respect'),
            '称谓表表示本章最终可用显示；具体揭名仍须等到对应行动，不得提前点破。'),
        continuity=(f'入场：{context.entry}', f'本章收束：{context.exit}'),
        creative_guidance=(f'本章目的：{context.purpose}', f'本章表达：{context.style}', *direction,
            *(f'{names[ref]}的声线：{voice}' for ref, voice in context.voices.items())),
        background=tuple(cue.text for cue in context.requirements if cue.placement == 'background'),
        guard_only=('未到目标事件位置的结果、知情与秘密不得提前兑现。',
                    '本章没有给出的跨章前史、承诺、秘密答案与持续状态不得新增。'))


def compile_hook(wire: Mapping[str, Any] | HookDesignWire, story: TargetStory,
                 *, length_budget: int) -> HookPlan:
    data = _wire(HookDesignWire, wire)
    event_ids = tuple(event.event_id for event in story.events)
    refs = tuple(_refs((beat.event_no,), event_ids, 'Hook beats',
                      path=('beats', index, 'event_no'))[0] for index, beat in enumerate(data.beats))
    _unique(refs, 'Hook beats', path=('beats',))
    allowed = _knowledge(data.allowed_knowledge, tuple(story.propositions), story.names,
                         'Hook allowed_knowledge', path=('allowed_knowledge',))
    selected = [event for event in story.events if event.event_id in refs]
    # Pydantic模型不可hash；认知比较用正式三元组，不能只比较命题而丢持有者/模式。
    key = lambda k: (k.holder, k.proposition_ref, k.mode)
    available = {key(k) for k in story.initial_knowledge} | {
        key(k) for event in selected for k in event.knowledge_changes}
    for index, known in enumerate(allowed):
        if key(known) not in available:
            _fail('Hook 知情授权不得超出选定目标事件与初始知情', ('allowed_knowledge', index))
    authorized = {key(k) for k in allowed}
    if any(k.holder == 'reader' and key(k) not in authorized for event in selected for k in event.knowledge_changes):
        _fail('Hook选定事件的读者披露必须显式列入allowed_knowledge', ('allowed_knowledge',))
    participants = {person for event in selected for person in event.participants}
    if set(data.allowed_displays) != participants:
        _fail('Hook allowed_displays必须恰好覆盖选定事件参与者', ('allowed_displays',))
    for person, display in data.allowed_displays.items():
        choices = {story.initial_displays[person], *(event.name_releases[person]
            for event in selected if person in event.name_releases)}
        if display not in choices:
            _fail(f'Hook {person} 的显示称谓未由初始状态或选定事件授权', ('allowed_displays', person))
    context = data.writer_context
    _unique((cue.item_no for cue in context.knowledge), 'Hook知识提示', path=('writer_context', 'knowledge'))
    for index, cue in enumerate(context.knowledge):
        if cue.item_no > len(allowed):
            _fail('Hook知识提示引用越界', ('writer_context', 'knowledge', index, 'item_no'))
        if allowed[cue.item_no - 1].holder not in participants | {'reader'}:
            _fail('Hook知识提示持有者不是选中参与者', ('writer_context', 'knowledge', index, 'item_no'))
    _unique((cue.rule_ref for cue in context.world_rules), 'Hook规则提示', path=('writer_context', 'world_rules'))
    _closed((cue.rule_ref for cue in context.world_rules), (r.rule_id for r in story.world_rules),
            'Hook规则提示', path=('writer_context', 'world_rules'))
    return HookPlan(contract_version='story-v1', event_refs=refs,
        beats=tuple(HookBeat(event_ref=ref, trigger=beat.trigger, choice=beat.choice,
                            action=beat.action, result=beat.result) for ref, beat in zip(refs, data.beats)),
        writer_context=context, organization=data.organization, emphasis=data.emphasis,
        ending=data.ending, reader_expectation=data.reader_expectation,
        allowed_knowledge=allowed, allowed_displays=dict(data.allowed_displays), length_budget=length_budget)


def project_hook_packet(story: TargetStory, hook: HookPlan, *,
                        length_band: tuple[float, float] = (0.8, 1.2)) -> HookPacket:
    events = {event.event_id: event for event in story.events}
    participants = tuple(dict.fromkeys(person for ref in hook.event_refs for person in events[ref].participants))
    names = {person: hook.allowed_displays[person] for person in participants}
    low, high = _band(hook.length_budget, length_band)
    return HookPacket(contract_version='story-v1', length_budget=hook.length_budget, min_chars=low, max_chars=high,
        names=names,
        must_realize=tuple(f'触发：{beat.trigger}；选择：{beat.choice}；行动：{beat.action}；结果：{beat.result}'
                           for beat in hook.beats),
        must_respect=(*(cue.text for cue in hook.writer_context.world_rules),
            *_knowledge_cues(hook.writer_context.knowledge, hook.allowed_knowledge, names)),
        continuity=(f'收束：{hook.ending}',), creative_guidance=(hook.organization, hook.emphasis,
            f'阅读期待：{hook.reader_expectation}', f'本段表达：{hook.writer_context.style}'),
        background=(), guard_only=('只能呈现选定事件与授权知情；不得新增结局、解释秘密或改写主体动机。',))


def _repair_item(rows: Mapping[str, list[tuple[tuple, ...]]], section: str,
                 item_no: int | None) -> tuple[tuple, ...]:
    if item_no is None or isinstance(item_no, bool) or item_no < 1:
        raise ValueError('设计问题没有可定位的材料引用，修订范围不足')
    items = rows.get(section, [])
    if item_no > len(items) or not items[item_no - 1]:
        raise ValueError('引用不是本owner可修订的自然材料，须明确扩大设计范围')
    return items[item_no - 1]


def chapter_packet_repair_paths(story: TargetStory, chapter_ordinal: int,
                               section: str, item_no: int | None) -> tuple[tuple, ...]:
    """送达材料位置→原ChapterPlan自然字段；不从问题文案猜修订范围。"""
    plan = story.chapter_plans[chapter_ordinal - 1]
    context = plan.writer_context
    chapter = _chapter(story, chapter_ordinal)
    events = {event.event_id: event for event in story.events}
    field = lambda *path: (tuple(path),)
    rows = {
        'entry_state': [field('writer_context', 'entry_state', n, 'text') for n in range(len(context.entry_state))],
        'exit_state': [field('writer_context', 'exit_state', n, 'text') for n in range(len(context.exit_state))],
        'must_realize': [],
        'must_respect': [field('writer_context', 'narration')],
        'continuity': [field('writer_context', 'entry'), field('writer_context', 'exit')],
        'creative_guidance': [field('writer_context', 'purpose'), field('writer_context', 'style')],
        'background': [],
    }
    for scene_no, scene in enumerate(plan.scenes):
        rows['creative_guidance'].append(tuple(('scenes', scene_no, key) for key in (
            'setting', 'time', 'goal', 'opposition', 'turn', 'realization')))
        for action_no, action in enumerate(scene.actions):
            rows['must_realize'].append(tuple(('scenes', scene_no, 'actions', action_no, key)
                for key in ('trigger', 'choice', 'action', 'response', 'result', 'transition')))
            if action.event_no is not None:
                rows['must_realize'].extend(() for _display in events[chapter.event_refs[action.event_no - 1]].name_releases.values())
    rows['creative_guidance'].extend(field('writer_context', 'voices', ref) for ref in context.voices)
    rows['must_respect'].extend(field('writer_context', 'world_rules', n, 'text') for n in range(len(context.world_rules)))
    rows['must_respect'].extend(field('writer_context', 'entry_knowledge', n, 'text') for n in range(len(context.entry_knowledge)))
    for n, cue in enumerate(context.requirements):
        rows['must_respect' if cue.placement == 'respect' else 'background'].append(
            field('writer_context', 'requirements', n, 'text'))
    # 包末尾称谓闸与guard_only是固定政策，不授权章owner改写。
    rows['must_respect'].append(())
    return _repair_item(rows, section, item_no)


def hook_packet_repair_paths(hook: HookPlan, section: str,
                            item_no: int | None) -> tuple[tuple, ...]:
    """Hook材料引用→HookDesignWire自然字段；选材和授权闭集保持冻结。"""
    field = lambda *path: (tuple(path),)
    rows = {
        'must_realize': [tuple(('beats', n, key) for key in ('trigger', 'choice', 'action', 'result'))
                         for n in range(len(hook.beats))],
        'must_respect': [field('writer_context', 'world_rules', n, 'text') for n in range(len(hook.writer_context.world_rules))]
            + [field('writer_context', 'knowledge', n, 'text') for n in range(len(hook.writer_context.knowledge))],
        'continuity': [field('ending')],
        'creative_guidance': [field('organization'), field('emphasis'), field('reader_expectation'), field('writer_context', 'style')],
    }
    return _repair_item(rows, section, item_no)


def _prompt(task: str, model: type[StrictModel], **inputs: Any) -> str:
    def plain(value):
        if isinstance(value, StrictModel):
            return value.model_dump(mode='json')
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value
    return (task + '\n只输出符合 schema 的 JSON。编号仅引用输入既有主体或1-based列表位置，'
        '不得输出编译器生成的ID、章号、路径、hash或归一化统计。\n' + json.dumps(
            {'inputs': plain(inputs), 'schema': model.model_json_schema()},
            ensure_ascii=False, sort_keys=True))


def build_naming_prompt(source, *, book_title: str = '') -> str:
    return _prompt('为全部人物选择目标姓名。源姓名有姓名位必须rename；纯称谓可keep并说明。'
        '同family_group同姓，不同家族不并姓。化名位完整，新旧名字不得冲突。', NamingWire,
        book_title=book_title, entities=source.entities, world_rules=source.world_rules)


def build_preserve_prompt(source, name_map: NameMap) -> str:
    return _prompt('根据SourceStory选择保持情节骨架、人物转变与必要因果的保存要求。'
        'required与satisfaction明确；不要逐源拍复制。每条世界规则显式keep/adapt/omit。'
        'keep/adapt都须用目标称谓写target_statement；keep保留规则机制，adapt另给改造理由。'
        'omit只说明理由，不写目标规则；compiler不复制源规则或按裸字面猜名称指代。'
        '每条源冲突显式保留含混或作局部一致化；一致化不得改变核心事件、人物关系、'
        '结局和关键秘密，不替未解悬念编答案。目标文本使用目标姓名。'
        '若实际源事实有漏抽/不支持/证据冲突，通过source_issues引用正式主体与证据，'
        'path必须相对source_ref那条正式记录明确字段，operation显式replace/remove/append。'
        '事件动作/在场者归章观察，时间和场景边界归全书理解；全书命题/实体不按证据章误派章观察。'
        '整条原生语义记录删除用空path与remove，禁止删除固定record字段或压缩身份列表。'
        '交回真实源owner；不要把抽取错误当作可改造的原作含混。'
        '把刺激、限制、权衡、选择、后果与信息前提完整交接，细节不升级为硬要求。',
        PreserveWire, source=source, target_names=name_map)


def preservation_brief(preservation: PreservationContract) -> dict[str, Any]:
    """源盲目标简报；不把源坐标、引文、source_bindings送进建模材料。"""
    brief = preservation.model_dump(mode='json', exclude={'source_bindings', 'world_rule_decisions'})
    brief['consistency_decisions'] = [decision.model_dump(mode='json', exclude={'conflict_no'})
                                      for decision in preservation.consistency_decisions]
    return brief


def build_target_model_prompt(preservation: PreservationContract, name_map: NameMap, *,
                              source_chapter_count: int, total_length_budget: int) -> str:
    return _prompt('构思一本目标小说的整书骨架：先组织人物处境、刺激/权衡/选择/结果、'
        '因果与故事时间，再分配章节。人物弧、世界规则、事实成立、角色知情与读者揭示分开。'
        '相同状态维度的不同对象用qualifier区分；只登记跨场持续约束，不为即时动作造状态。'
        'actual/planned/expected/conditional各自占槽，计划不能覆盖实际；计划完成或取消须显式unset原模态。'
        '过去事件不能倒写当前状态。事件requires是必要因果，after是故事时间；不是源叙述顺序。'
        'known仅用于established命题；错误信念用believed，后续纠正单列变化。'
        'initial_displays为每个实体给开场可见称呼；隐藏身份/未获名人物用纯称谓，'
        '在正式揭名事件上以name_releases释放目标姓名，不能因entity存在就提前点名。'
        '每项required至少一个事件realizes；源未知保持未知。章节数量严格等于输入。'
        '这里只建完整因果骨架及章边界，不在一次回答中输出全部细拍正文。',
        TargetModelWire, preservation=preservation_brief(preservation),
        target_names={ref: name.primary for ref, name in name_map.names.items()},
        source_chapter_count=source_chapter_count, total_length_budget=total_length_budget)


def build_chapter_prompt(model: TargetModelDraft, preservation: PreservationContract,
                         *, chapter_ordinal: int) -> str:
    chapter = _chapter(model, chapter_ordinal)
    events = {e.event_id: e for e in model.events}
    return _prompt('把冻结的本章目标事件展开为可演出的连续场景，保住触发、权衡、'
        '行动/反应、结果、时空与感知渠道。event_no是本章events列表1-based位置，'
        '每项恰承载一次并保持顺序；无跨章效应的衔接行动明确event_no=null。'
        '所有场景/行动自然文本用该事件当时许可称谓；trigger/choice/行动须完整演出冻结骨架。'
        'writer_context是本章唯一送给写手的自然材料：按本章许可显示、知情与揭示时点重述，'
        '包含安全的入出状态、认知、规则和保存要求提示；不得照抄含后章秘密答案的全书文本。'
        'SnapshotCue.item_no引用chapter对应entry_state/exit_state/entry_knowledge列表的1-based项。'
        'world_rules引用rule_id，requirements只引用本章events.realizes；可不给不适用/不宜送达项。'
        '提示只能用该正式项已确立的信息，隐藏事实须省略或抽象成禁区，不能把完整答案放进禁令。'
        'compiler不会按字面替换名字，也不会自动送达全书状态、命题、规则、约束或风格原文。'
        '入场/出场事实和知情已冻结，不能新造后章依赖的前史、关系、承诺、秘密或状态。'
        '能定位的骨架修订使用model_issues；无法定位受影响事件或组件时才填cross_boundary_changes并停下，'
        '不能在正文说明中偷改。'
        '缺必要原因/知情前提/持久条件时，model_issues必须显式给component、operation和item_no，'
        '事件组件以event_no引用本章事件；propositions/initial_state/initial_knowledge等全书组件用event_no=null。'
        'replace引用既有数组条目的1-based item_no，append用item_no=null并保持既有位置。'
        '需要新命题及其知情条件时分别报告propositions append和对应knowledge组件append，'
        '由整书owner共同补齐；不在本章暗造新持久条件。'
        'chapter_entry/chapter_exit只可replace边界表达。'
        '这些typed问题交回全书目标建模，'
        '不是新增状态就不伪造状态槽来报告。'
        '场景参与者/视角只能取本章既有人物，narrator可用。', ChapterPlanWire,
        chapter=chapter, events=[events[ref] for ref in chapter.event_refs],
        world_rules=model.world_rules, propositions=model.propositions,
        preservation=preservation_brief(preservation),
        narration=model.narration, style=model.style, character_voices=model.character_voices)


def build_hook_prompt(story: TargetStory, *, length_budget: int) -> str:
    return _prompt('从目标故事选择楔子材料并安排组织、重心、收束和阅读期待。'
        '不创造新的事件/前史/动机，不以冲击力为由提前揭示未授权秘密。'
        'beats.event_no为整书events列表1-based序号；每个选中事件用当段许可称谓重述触发/选择/演出/结果。'
        'allowed_knowledge必须来自选定事件或初始知情。'
        'writer_context.knowledge的item_no引用allowed_knowledge列表；规则提示引用正式rule_id。'
        'writer_context和beats是仅送本段的自然材料，禁止抄入全书未来答案或隐藏身份；'
        '不适用/尚不应送达的真相省略，禁区只抽象描述，不包含完整秘密答案。'
        'compiler不复制全书自然文本，也不按裸字面替换名字。'
        'allowed_displays逐选中参与者明确许可称谓，只能取initial_displays或选中事件的name_releases。'
        '不要输出正文，不改正文设计。', HookDesignWire,
        events=story.events, propositions=story.propositions, initial_knowledge=story.initial_knowledge,
        initial_displays=story.initial_displays,
        world_rules=story.world_rules, target_names={ref: n.primary for ref, n in story.names.items()},
        narration=story.narration, style=story.style, length_budget=length_budget)
