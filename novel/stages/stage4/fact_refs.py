#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from novel.core.facts import first_reveal_chapter, relation_visibility_gates
from novel.errors import RefusedByStructure


def catalog(global_g: dict[str, Any], blueprints: dict[str, Any],
            main_text: list[dict[str, Any]], *, plan_units: Any = None) -> dict[str, Any]:
    """规划响应可引用的唯一目录；事实原子直接带 claim 与归属，不再做 event join。"""
    chapters = sorted(int(row.get('chapter_ordinal') or 0) for row in main_text)
    atoms = {
        str(atom.get('fact_id')): {
            'chapter': int(bp.get('chapter_ordinal') or 0),
            'claim': atom.get('claim'), 'kind': atom.get('kind'),
            'obligation': atom.get('obligation'),
            'source_beat_ref': atom.get('source_beat_ref'),
        }
        for bp in (blueprints.get('blueprints') or [])
        for atom in (bp.get('fact_atoms') or []) if atom.get('fact_id')
    }
    stages = {
        str(beat.get('beat_id')): int(bp.get('chapter_ordinal') or 0)
        for bp in (blueprints.get('blueprints') or [])
        for beat in (bp.get('beats') or []) if beat.get('beat_id')
    }
    materials = {
        str(hinge.get('hinge_id')): int(bp.get('chapter_ordinal') or 0)
        for bp in (blueprints.get('blueprints') or [])
        for beat in (bp.get('beats') or [])
        for hinge in (beat.get('hinges') or []) if hinge.get('hinge_id')
    }
    entities, canon = global_g.get('entities') or {}, global_g.get('canon') or {}
    gates = relation_visibility_gates(entities, canon.get('secrets') or [])
    relations = {row.get('relation_id'): {
        'from': gates.get(row.get('relation_id')),
        'to': row.get('valid_through_chapter')}
        for row in (entities.get('relations') or []) if row.get('relation_id')}
    rules = {row.get('rule_id'): sorted(
        int(version.get('effective_from_chapter') or 0)
        for version in (row.get('versions') or []))
        for row in (canon.get('world_rules') or []) if row.get('rule_id')}
    secrets = {row.get('secret_id'): first_reveal_chapter(row)
               for row in (canon.get('secrets') or []) if row.get('secret_id')}
    return {
        'chapters': chapters, 'fact_atoms': atoms, 'stages': stages,
        'materials': materials,
        'characters': sorted((entities.get('characters') or {}).keys()),
        'relations': relations, 'world_rules': rules, 'secrets': secrets,
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
                kind, key = ref.get('kind'), ref.get('ref')
                prefix = f'ch{ordinal}.{goal_name}:{kind}:{key}'
                if kind == 'fact_atom':
                    atom = refs['fact_atoms'].get(key)
                    if not atom or atom['chapter'] != ordinal:
                        errors.append(f'{prefix}: fact atom absent or belongs elsewhere')
                elif kind == 'character' and key not in refs['characters']:
                    errors.append(f'{prefix}: unknown character')
                elif kind == 'world_rule':
                    versions = refs['world_rules'].get(key)
                    if not versions or min(versions) > ordinal:
                        errors.append(f'{prefix}: rule not effective in this chapter')
                elif kind == 'secret':
                    reveal = refs['secrets'].get(key)
                    if key not in refs['secrets'] or (
                            intent != 'withhold' and (reveal is None or ordinal < reveal)):
                        errors.append(f'{prefix}: secret outside reveal window')
                elif kind == 'relation':
                    window = refs['relations'].get(key)
                    if not window or window.get('from') is None or window['from'] > ordinal \
                            or (window.get('to') is not None and ordinal > window['to']):
                        errors.append(f'{prefix}: relation outside validity window')
                elif kind == 'setting' and key not in refs['setting']:
                    errors.append(f'{prefix}: unknown setting field')
                elif kind == 'chapter_boundary':
                    allowed = {f'chapter:{ordinal}', f'chapter:{ordinal}:start',
                               f'chapter:{ordinal}:end'}
                    if key not in allowed:
                        errors.append(f'{prefix}: invalid boundary')
                elif kind not in {'fact_atom', 'character', 'world_rule', 'secret',
                                   'relation', 'setting', 'chapter_boundary'}:
                    errors.append(f'{prefix}: unsupported kind')
    if errors:
        raise RefusedByStructure(
            f'计划 typed refs 无法从 Stage1/2 复算，{len(errors)} 处：'
            + '；'.join(errors[:12]))
