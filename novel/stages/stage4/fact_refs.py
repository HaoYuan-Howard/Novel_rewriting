#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from novel.errors import RefusedByStructure


def catalog(
    global_g: dict[str, Any],
    blueprints: dict[str, Any],
    main_text: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stable identifiers and their recomputable chapter/reveal boundaries."""
    chapters = sorted(int(row.get('chapter_ordinal') or 0) for row in main_text)
    events = {
        beat.get('beat_id'): int(bp.get('chapter_ordinal') or 0)
        for bp in (blueprints.get('blueprints') or [])
        for beat in (bp.get('beats') or [])
        if beat.get('beat_id')
    }
    entities = global_g.get('entities') or {}
    characters = sorted((entities.get('characters') or {}).keys())
    relations = {
        row.get('relation_id'): {
            'from': row.get('valid_from_chapter'), 'to': row.get('valid_to_chapter')}
        for row in (entities.get('relations') or []) if row.get('relation_id')
    }
    canon = global_g.get('canon') or {}
    rules = {
        row.get('rule_id'): sorted(
            int(version.get('effective_from_chapter') or 0)
            for version in (row.get('versions') or []))
        for row in (canon.get('world_rules') or []) if row.get('rule_id')
    }
    secrets = {
        row.get('secret_id'): min(
            (int(event.get('chapter') or 0)
             for event in (row.get('reader_reveal_events') or [])), default=None)
        for row in (canon.get('secrets') or []) if row.get('secret_id')
    }
    return {
        'chapters': chapters,
        'events': events,
        'characters': characters,
        'relations': relations,
        'world_rules': rules,
        'secrets': secrets,
        'setting': ['时代背景', '世界观'],
    }


def validate_plan_refs(plan: dict[str, Any], refs: dict[str, Any]) -> None:
    errors: list[str] = []
    chapter_set = set(refs['chapters'])
    for chapter in plan.get('output_chapters') or []:
        ordinal = int(chapter.get('output_ordinal') or 0)
        if ordinal not in chapter_set:
            errors.append(f'ch{ordinal}: chapter boundary does not exist')
        for goal_name in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
            goal = chapter.get(goal_name) or {}
            intent = goal.get('intent')
            for ref in goal.get('fact_refs') or []:
                kind = ref.get('kind')
                key = ref.get('ref')
                at = ref.get('chapter')
                prefix = f'ch{ordinal}.{goal_name}:{kind}:{key}'
                if at != ordinal:
                    errors.append(f'{prefix}: ref.chapter={at}, expected {ordinal}')
                    continue
                if kind == 'event':
                    if refs['events'].get(key) != ordinal:
                        errors.append(f'{prefix}: event absent or belongs to another chapter')
                elif kind == 'character':
                    if key not in refs['characters']:
                        errors.append(f'{prefix}: unknown character')
                elif kind == 'world_rule':
                    versions = refs['world_rules'].get(key)
                    if not versions or min(versions) > ordinal:
                        errors.append(f'{prefix}: rule not effective in this chapter')
                elif kind == 'secret':
                    reveal = refs['secrets'].get(key)
                    if key not in refs['secrets']:
                        errors.append(f'{prefix}: unknown secret')
                    elif intent != 'withhold' and (reveal is None or ordinal < reveal):
                        errors.append(f'{prefix}: secret is outside its reveal window')
                elif kind == 'relation':
                    window = refs['relations'].get(key)
                    if not window:
                        errors.append(f'{prefix}: unknown relation')
                    elif ((window.get('from') or 1) > ordinal
                          or (window.get('to') is not None and ordinal > window['to'])):
                        errors.append(f'{prefix}: relation is outside its validity window')
                elif kind == 'setting':
                    if key not in refs['setting']:
                        errors.append(f'{prefix}: unknown setting field')
                elif kind == 'chapter_boundary':
                    allowed = {
                        f'chapter:{ordinal}', f'chapter:{ordinal}:start',
                        f'chapter:{ordinal}:end',
                    }
                    if key not in allowed:
                        errors.append(
                            f'{prefix}: boundary must be one of {sorted(allowed)}')
                else:
                    errors.append(f'{prefix}: unsupported kind')
    if errors:
        raise RefusedByStructure(
            f'计划的 typed fact refs 无法从 stage1/stage2 复算，{len(errors)} 处：'
            + '；'.join(errors[:12]))
