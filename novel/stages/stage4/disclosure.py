# -*- coding: utf-8 -*-
"""把已裁决的知识时点投影到当前场景；不按源序猜读者何时知道。

支持事实与触发事实不同：多条证据可以合力证明一项知识，只有 typed trigger
确定本章的披露场景。伏笔只取关联的表现事实，秘密答案不进入埋点章的任何字段。
本模块不读写文件；上游必须先经 stage2.knowledge_links.require_current 核验来源。
"""
from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass
from typing import AbstractSet, Any

from contracts.schema.knowledge_links import KnowledgeLinks, KnowledgeLinkRecord
from novel.errors import RefusedByStructure
from novel.llm.agent_runs import canonical_sha256


@dataclass(frozen=True, order=True)
class _Boundary:
    chapter: int
    scene: int


@dataclass
class DisclosureProjection:
    facts: dict[str, Any]
    scenes: list[dict[str, Any]]
    windows: list[dict[str, Any]]
    goal_targets: dict[tuple[str, str], str]


def project(
    out: dict[str, Any], scenes: list[dict[str, Any]], facts: dict[str, Any],
    grounding: dict[str, Any], atoms: dict[str, dict[str, Any]], display: dict[str, str],
    association: dict[str, Any], *, needed_characters: set[str],
    materials: dict[str, dict[str, Any]],
) -> DisclosureProjection:
    links = KnowledgeLinks.model_validate(association)
    subjects = {row.obligation_id: row for row in links.scope.obligations}
    indexed = {(subjects[row.obligation_id].kind, subjects[row.obligation_id].knowledge_ref,
                subjects[row.obligation_id].chapter_ordinal): row for row in links.links}
    ordinal = int(out['output_ordinal'])
    slots = [str(scene['slot']) for scene in out['scenes']]
    projected = copy.deepcopy(scenes)
    for scene in projected:
        scene['knowledge'] = []
    result = DisclosureProjection(
        facts={**copy.deepcopy(facts), 'secrets': [], 'relations': [], 'world_rules': [],
               'setting': {'时代背景': None, '世界观': None}},
        scenes=projected, windows=[], goal_targets={})
    goal_refs = {(str(ref['kind']), str(ref['ref']))
                 for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal')
                 for ref in out[key]['fact_refs']}
    current_facts = {str(ref) for scene in out['scenes']
                     for key in ('land_fact_refs', 'context_fact_refs',
                                 'entry_state_refs', 'exit_state_refs')
                     for ref in scene.get(key) or []}

    def record(kind: str, ref: str, chapter: int | None = None) -> KnowledgeLinkRecord:
        found = [row for (candidate_kind, candidate_ref, candidate_chapter), row in indexed.items()
                 if candidate_kind == kind and candidate_ref == ref
                 and (chapter is None or chapter == candidate_chapter)]
        if len(found) != 1:
            raise RefusedByStructure(f'当前知识关联未唯一覆盖 {kind}:{ref}:{chapter}')
        return found[0]

    def boundary(row: KnowledgeLinkRecord) -> _Boundary:
        subject = subjects[row.obligation_id]
        if row.resolution.kind == 'unresolved':
            raise RefusedByStructure('未决关联不能用于构造写手材料')
        if row.resolution.kind == 'at_entry':
            return _Boundary(subject.chapter_ordinal, -1)
        if subject.chapter_ordinal != ordinal:
            return _Boundary(subject.chapter_ordinal, 0)
        ref = row.resolution.trigger_fact_ref
        matches = [index for index, scene in enumerate(out['scenes'])
                   if ref in scene['land_fact_refs']]
        if len(matches) != 1:
            raise RefusedByStructure(f'知识触发事实 {ref} 必须在本章恰落一个场景')
        at = matches[0]
        # 场景引用也是材料入口。不能把揭示事实以 context/entry/exit 标签提前塞入。
        for index, scene in enumerate(out['scenes'][:at]):
            early = {str(value) for key in ('context_fact_refs', 'entry_state_refs', 'exit_state_refs')
                     for value in scene.get(key) or []}
            early.update(fact_ref for material_ref in scene['material_refs']
                         for fact_ref in materials[material_ref]['source_fact_refs'])
            if ref in early:
                raise RefusedByStructure(
                    f'场景 {slots[index]} 在触发场景 {slots[at]} 前引用受限事实 {ref}；'
                    '须修订场景引用后免费重建上下文')
        return _Boundary(ordinal, at)

    def permitted(at: _Boundary) -> list[str]:
        if at.chapter > ordinal:
            return []
        return slots if at.chapter < ordinal else slots[max(at.scene, 0):]

    def eligible(kind: str, ref: str, row: KnowledgeLinkRecord,
                 characters: AbstractSet[str] = frozenset()) -> bool:
        return ((kind, ref) in goal_refs or bool(characters & needed_characters)
                or row.resolution.kind != 'unresolved'
                and bool(set(row.resolution.fact_refs) & current_facts))

    def window(ref: str, content: str, at: _Boundary, row: KnowledgeLinkRecord | None = None) -> None:
        allowed = permitted(at)
        if not allowed:
            return
        result.windows.append({'subject_ref': ref, 'content': content, 'allowed_slots': allowed})
        if (row is not None and row.resolution.kind == 'at_facts'
                and subjects[row.obligation_id].chapter_ordinal == ordinal):
            trigger = row.resolution.trigger_fact_ref
            if trigger not in atoms:
                raise RefusedByStructure(f'知识触发事实不存在：{trigger}')
            result.windows.append({'subject_ref': trigger, 'content': atoms[trigger]['claim'],
                                   'allowed_slots': allowed})

    def scene_item(kind: str, content: str, at: _Boundary, *, known_by: Sequence[str] = (),
                   usage: str = 'reveal') -> None:
        if at.chapter != ordinal or at.scene < 0:
            return
        projected[at.scene]['knowledge'].append({
            'kind': kind, 'content': content, 'usage': usage, 'known_by': list(known_by)})

    def target(kind: str, ref: str, at: _Boundary, *, setup: bool = False) -> None:
        allowed = permitted(at)
        if not allowed:
            return
        if setup:
            text = f'只在场景 {allowed[0]} 表现已给出的伏笔事实，不解释背后真相'
        elif at.chapter < ordinal or at.scene < 0:
            text = '按章首已开放的事实执行，保持其因果与状态约束'
        else:
            text = f'相关知识在场景 {allowed[0]} 起开放；之前只执行各场允许的材料'
        result.goal_targets[(kind, ref)] = text

    canon = grounding['canon']
    entities = grounding['entities']
    secret_boundaries: dict[str, _Boundary] = {}
    for secret in canon['secrets']:
        ref = secret['secret_id']
        reveal = record('secret_reveal', ref)
        at = boundary(reveal)
        secret_boundaries[ref] = at
        subjects_by_event = {event['subject'].get('char_id') for event in secret['knowledge_events']
                             if event['subject']['kind'] == 'character'}
        setup = next((row for (kind, key, chapter), row in indexed.items()
                      if kind == 'secret_setup' and key == ref and chapter == ordinal), None)
        if setup is not None:
            # 本章只能收到外在表现；这里没有读取或复制 secret.fact。
            setup_at = boundary(setup)
            target('secret', ref, setup_at, setup=True)
            for fact_ref in setup.resolution.fact_refs:
                if fact_ref not in atoms:
                    raise RefusedByStructure('伏笔关联引用不存在的事实')
                occurrence = [i for i, scene in enumerate(out['scenes'])
                              if fact_ref in scene['land_fact_refs']]
                if len(occurrence) != 1:
                    raise RefusedByStructure('伏笔表现事实必须在本章恰落一个场景')
                scene_item('setup', atoms[fact_ref]['claim'], _Boundary(ordinal, occurrence[0]), usage='setup')
        if not permitted(at) or not eligible('secret', ref, reveal, subjects_by_event):
            continue

        def known_by(moment: _Boundary) -> list[str]:
            names = []
            for event in secret['knowledge_events']:
                subject = event['subject']
                key = (subject['char_id'] if subject['kind'] == 'character'
                       else 'group_' + canonical_sha256(subject)[:24])
                if boundary(record('knowledge_event', f'{ref}/{key}')) > moment:
                    continue
                name = display.get(key) if subject['kind'] == 'character' else subject['label']
                if name is not None and name not in names:
                    names.append(name)
            return names

        if at < _Boundary(ordinal, 0):
            result.facts['secrets'].append({'secret_id': ref, 'fact': secret['fact'],
                'known_by': known_by(_Boundary(ordinal, -1)), 'reader_knows_by_now': True})
        else:
            scene_item('secret', secret['fact'], at, known_by=known_by(at))
        window(ref, secret['fact'], at, reveal)
        target('secret', ref, at)
        # 读者已知以后发生的知情人变化，仍只在其绑定场景更新，不提前扩大章首 known_by。
        events = [boundary(row) for (kind, key, _chapter), row in indexed.items()
                  if kind == 'knowledge_event' and key.startswith(ref + '/')]
        for event_at in sorted(set(events)):
            if event_at.chapter == ordinal and event_at.scene >= 0 and event_at > at:
                scene_item('secret', secret['fact'], event_at, known_by=known_by(event_at), usage='known')

    available_rules = {rule['rule_id'] for rule in facts['world_rules']}
    for rule in canon['world_rules']:
        # 关联决定只开放知识时点，不能越过既有的称名时点闸门。
        if rule['rule_id'] not in available_rules:
            continue
        versions = [version for version in rule['versions'] if version['effective_from_chapter'] <= ordinal]
        if not versions:
            continue
        version = max(versions, key=lambda item: item['effective_from_chapter'])
        ref = f'{rule["rule_id"]}/{version["effective_from_chapter"]}'
        world = record('world_rule', ref)
        reader = record('world_rule_reveal', ref)
        at = max(boundary(world), boundary(reader))
        if not eligible('world_rule', rule['rule_id'], world) and not eligible('world_rule', rule['rule_id'], reader):
            continue
        if at < _Boundary(ordinal, 0):
            result.facts['world_rules'].append({'rule_id': rule['rule_id'], 'kind': rule['kind'],
                                               'description': version['desc']})
        else:
            scene_item('world_rule', version['desc'], at)
        window(ref, version['desc'], at, reader)
        target('world_rule', rule['rule_id'], at)

    for relation in entities['relations']:
        ref = relation['relation_id']
        if (relation['valid_through_chapter'] is not None
                and relation['valid_through_chapter'] < ordinal):
            continue
        if relation['subject_id'] not in display or relation['object_id'] not in display:
            continue
        world = record('relation', ref)
        if not eligible('relation', ref, world, {relation['subject_id'], relation['object_id']}):
            continue
        visibility = relation['visibility']
        reader = record('relation_visibility', ref) if visibility['kind'] == 'public' else None
        reader_at = (boundary(reader) if reader is not None else secret_boundaries.get(visibility['secret_id']))
        if reader_at is None:
            raise RefusedByStructure('关系的秘密披露依赖没有当前关联')
        at = max(boundary(world), reader_at)
        subject, obj = display[relation['subject_id']], display[relation['object_id']]
        content = f'{subject}与{obj}：{relation["predicate"]}'
        if at < _Boundary(ordinal, 0):
            result.facts['relations'].append({'subject': subject, 'predicate': relation['predicate'], 'object': obj})
        else:
            scene_item('relation', content, at)
        window(ref, content, at, reader)
        target('relation', ref, at)

    for cid in sorted(needed_characters & set(entities['characters']) & set(display)):
        fate = entities['characters'][cid].get('fate')
        if fate is None:
            continue
        row = record('character_state', cid)
        at = boundary(row)
        content = f'{display[cid]}：{fate}'
        if at < _Boundary(ordinal, 0):
            scene_item('character_state', content, _Boundary(ordinal, 0), usage='known')
        else:
            scene_item('character_state', content, at)
        window('state:' + cid, content, at, row)

    for key in ('时代背景', '世界观'):
        row = record('setting', key)
        at = boundary(row)
        content = canon['setting'][key]
        if at < _Boundary(ordinal, 0):
            result.facts['setting'][key] = content
        else:
            scene_item('setting', content, at)
        window('setting:' + key, content, at, row)
        target('setting', key, at)

    # 同一受限事实可能同时是知识正文和 Blueprint 触发事实；不合并不同文字或时点。
    unique = {(row['subject_ref'], row['content'], tuple(row['allowed_slots'])): row for row in result.windows}
    result.windows = list(unique.values())
    return result
