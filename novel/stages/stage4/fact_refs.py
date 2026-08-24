#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from novel.core.facts import first_reveal_chapter, relation_visibility_gates
from novel.errors import RefusedByStructure


def catalog(
    global_g: dict[str, Any],
    blueprints: dict[str, Any],
    main_text: list[dict[str, Any]],
    *,
    plan_units: dict[Any, list[list[str]]],
) -> dict[str, Any]:
    """Stable identifiers and their recomputable chapter/reveal boundaries.

    ⚠⚠ `plan_units` 是必填而不是可选：少了它，目录会退回「只有 `bNN_MM → 章号`」
    那一版，而规划者的骨架块**按设计不下发 `beat_id`**
    （`project_blueprints_for_planner` 的那条注释）——两块拼起来，模型手里就有了
    一堆没有含义的 id 和一堆没有 id 的正文，中间那道 join 谁也没给它。
    而 `PLAN_SYS` 又要求「`open_hook_goal` 至少引用**首个写作单元**中的一条 event」，
    `goal_landing` 还把它做成 HARD ⇒ **一条按构造无法满足的阻断级判据**。
    实测（常春杜若，2026-08-17 首跑）：50 个边界目标错 20 个，
    而那 20 条的 `rationale` 全都正确描述了目标单元里的那一拍——模型读得懂骨架正文，
    只是没法把正文映回 b 号。⇒ 目录必须把这道 join 一起给出来。
    给出 join **不等于**给了「重新编组」的抓手：`plan_beats[]` 只收 `pNN_MM`，
    而拆开/换序一个源单元由 `structure_units` 那条 HARD 挡着。
    """
    chapters = sorted(int(row.get('chapter_ordinal') or 0) for row in main_text)
    events = {
        beat.get('beat_id'): int(bp.get('chapter_ordinal') or 0)
        for bp in (blueprints.get('blueprints') or [])
        for beat in (bp.get('beats') or [])
        if beat.get('beat_id')
    }
    # ⚠ 用与骨架块**同一个**编号函数，不在这里手拼 `f'p{o:02d}_{i:02d}'`：
    #   两处各拼一份，就是同一个编号的第二个落点。
    from novel.stages.stage4.beats import plan_beat_id
    event_units = {
        bid: plan_beat_id(ordinal, index)
        for ordinal, units in (plan_units or {}).items()
        for index, unit in enumerate(units, 1)
        for bid in unit
    }
    entities = global_g.get('entities') or {}
    canon = global_g.get('canon') or {}
    characters = sorted((entities.get('characters') or {}).keys())
    gates = relation_visibility_gates(entities, canon.get('secrets') or [])
    relations = {
        row.get('relation_id'): {
            'from': gates.get(row.get('relation_id')),
            'to': row.get('valid_through_chapter')}
        for row in (entities.get('relations') or []) if row.get('relation_id')
    }
    rules = {
        row.get('rule_id'): sorted(
            int(version.get('effective_from_chapter') or 0)
            for version in (row.get('versions') or []))
        for row in (canon.get('world_rules') or []) if row.get('rule_id')
    }
    # ⚠ 取**首次**揭示章，与 `context.py` 的下发闸门同一个函数。用 `min(全部事件)`
    #   会在分两次揭示的 secret 上给出同一个数（载体不许有事件早于 first），
    #   但两处一旦各写一份式子，下一次改判向就会漏掉其中一处。
    secrets = {
        row.get('secret_id'): first_reveal_chapter(row)
        for row in (canon.get('secrets') or []) if row.get('secret_id')
    }
    return {
        'chapters': chapters,
        'events': events,
        # 这条 event 属于哪个源内容单元。⚠ 与 `events` 分成两个键而不是把值改成对象：
        # `validate_plan_refs` 拿 `events[key] == ordinal` 判归属，换形状要改两处判定，
        # 而这一次要补的只是**下发面缺的那道 join**，不是引用契约。
        'event_units': event_units,
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
                    elif (window.get('from') is None or window['from'] > ordinal
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
