"""章审必要主体与来源选择；未来的后台真相可以供审阅核对，不能当作写作许可。"""
from __future__ import annotations


from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.dependencies import ScopeSelection
from novel.consumption import plot_global_projection, select_scope_input
from novel.stages.stage2.paragraphs import paragraph_range_of


def global_subjects(raw: dict, selected_ids) -> list[dict]:
    values = {
        **{('relation', row['relation_id']): row for row in raw['relations']},
        **{('secret', row['secret_id']): row for row in raw['arc_secrets']},
        **{('world_rule', row['rule_id']): row for row in raw['world_rules']},
        **{('world_rule', f"{rule['rule_id']}/{version['version_id']}"):
            {'rule_id': rule['rule_id'], 'kind': rule['kind'], 'version': version}
            for rule in raw['world_rules'] for version in rule['versions']},
    }
    for field in ('era', 'social_context'):
        values['setting', field] = {'content': raw['setting'][field],
                                    'evidence_refs': raw['setting'][field + '_evidence_refs']}
    # fact_ref 的权威命题与证据一起成为真实审阅输入，不能留下只有指针的空材料。
    for secret in raw['arc_secrets']:
        ref = secret['fact_ref']
        if ref:
            target = values['relation', ref['relation_id']] if ref['kind'] == 'relation' else values[
                'world_rule', f"{ref['rule_id']}/{ref['version_id']}"]
            values['secret', secret['secret_id']] = {**secret, 'referenced_fact': target}
    return _subjects(values, (selected.split(':', 1) for selected in selected_ids), raw['evidence_anchors'])


def character_subjects(raw: dict, selected_ids) -> list[dict]:
    """人物登记与实际使用的共享证据；不在审阅材料重复原文。"""
    if not isinstance(raw, dict) or not isinstance(raw.get('characters'), dict):
        raise ValueError('人物审阅材料缺少当前正式名册')
    values = {('character', ref): row for ref, row in raw['characters'].items()}
    return _subjects(values, (('character', ref) for ref in selected_ids), raw['evidence_anchors'])


def _subjects(values, selected, anchors) -> list[dict]:
    result = []
    for kind, ref in selected:
        if (kind, ref) not in values:
            raise ValueError(f'必要审阅主体缺失：{kind}:{ref}')
        needed = set()
        def visit(value):
            if isinstance(value, list):
                for row in value:
                    visit(row)
            elif isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith('evidence_refs'):
                        needed.update(child)
                    else:
                        visit(child)
        visit(values[kind, ref])
        if not needed <= anchors.keys():
            raise ValueError('必要主体缺少其共享证据定义')
        result.append({'kind': kind, 'ref': ref, 'value': values[kind, ref],
            'evidence': [dict(evidence_ref=key, **anchors[key]) for key in sorted(needed)]})
    return result


def character_references(value) -> set[str]:
    """只枚举正式人物引用字段，不从剧情正文猜人名或人物身份。"""
    if isinstance(value, list):
        return set().union(*(character_references(row) for row in value), set())
    if not isinstance(value, dict):
        return set()
    refs = set()
    for key, child in value.items():
        scalar = (key.endswith('_char_id') or key in ('char_id', 'subject_id', 'object_id', 'by')
                  or key == 'ref' and value.get('kind') == 'character')
        if scalar and isinstance(child, str) and child.startswith('char_'):
            refs.add(child)
        if key.endswith('_char_ids') and isinstance(child, list):
            refs.update(child)
        refs.update(character_references(child))
    return refs


def selections(snapshot, scope: str) -> tuple[ScopeSelection, ...]:
    selected = set(snapshot.ordinals if scope == 'cross_chapter' else [int(scope.split(':')[1])])
    knowledge = snapshot.knowledge_scope(scope)
    global_refs = {(row['kind'], row['ref']) for ordinal in selected
                   for row in plot_global_projection(snapshot.inputs[STAGE2_GLOBAL_GROUNDING], ordinal)}
    global_refs.update((row.subject_ref.kind, row.subject_ref.ref) for row in knowledge.obligations
                       if row.subject_ref.kind not in ('beat', 'state', 'character'))
    bp = snapshot.blueprints.blueprints
    origin = {(kind, getattr(row, field)): chapter.chapter_ordinal for chapter in bp
              for kind, field in (('beat', 'beat_id'), ('state', 'state_id'))
              for row in (chapter.beats if kind == 'beat' else chapter.states)}
    support_chapters = {origin[r.kind, r.ref] for item in knowledge.obligations
                        for r in item.entry_subject_refs if (r.kind, r.ref) in origin} - selected
    # 知识关联允许选择的远端候选必须有正文和原文，不能只给一个可点击的 ID。
    result = [ScopeSelection(input_rel=STAGE2_GLOBAL_GROUNDING, projection='plot_global_review',
        chapter_ordinal=min(selected), selected_ids=tuple(f'{kind}:{ref}' for kind, ref in sorted(global_refs)))]
    for ordinal in sorted(support_chapters):
        result.append(ScopeSelection(input_rel=STAGE2_CHAPTER_BLUEPRINTS,
                                     projection='plot_chapter', chapter_ordinal=ordinal))
    global_material = global_subjects(snapshot.inputs[STAGE2_GLOBAL_GROUNDING], result[0].selected_ids)
    source_chapters = support_chapters - selected
    ranges = {}
    texts = {row['chapter_ordinal']: row['text'] for row in snapshot.inputs[STAGE1_MAIN_TEXT]}
    for row in global_material:
        for anchor in row['evidence']:
            ordinal = anchor['chapter_ordinal']
            if ordinal not in selected | source_chapters:
                start, end = paragraph_range_of(texts[ordinal], anchor['start'], anchor['end'])
                ranges.setdefault(ordinal, set()).add(f'p:{start}:{end}')
    # 跨章包已经含全部蓝图正文；章审只加未在其他材料中出现的继承状态。
    if scope != 'cross_chapter':
        selection = ScopeSelection(input_rel=STAGE2_CHAPTER_BLUEPRINTS,
                                    projection='plot_inherited_states', chapter_ordinal=min(selected))
        result.append(selection)
        inherited = select_scope_input(selection, snapshot.inputs[STAGE2_CHAPTER_BLUEPRINTS])
        for row in inherited:
            ordinal = row['chapter_ordinal']
            if ordinal not in selected | source_chapters:
                ranges.setdefault(ordinal, set()).update(
                    f"p:{e['start_para']}:{e['end_para']}" for e in row['evidence'])
        for row in inherited:
            for binding in row.get('actor_bindings', []):
                for support in [binding, *([binding['public_state']] if binding.get('public_state') else [])]:
                    ordinal = support['chapter_ordinal']
                    if ordinal not in selected | source_chapters:
                        ranges.setdefault(ordinal, set()).update(
                            f"p:{e['start_para']}:{e['end_para']}" for e in support['evidence_anchors'])
        inherited_refs = {('state', row['state']['state_id']) for row in inherited}
        requested = {(row.subject_ref.kind, row.subject_ref.ref) for row in knowledge.obligations
                     if row.kind in ('reader_reveal', 'character_knowledge')
                     and (row.subject_ref.kind, row.subject_ref.ref) in origin
                     and origin[row.subject_ref.kind, row.subject_ref.ref] not in selected | support_chapters}
        requested -= inherited_refs
        if requested:
            selection = ScopeSelection(input_rel=STAGE2_CHAPTER_BLUEPRINTS, projection='plot_subjects',
                chapter_ordinal=min(selected), selected_ids=tuple(f'{kind}:{ref}' for kind, ref in sorted(requested)))
            result.append(selection)
            for row in select_scope_input(selection, snapshot.inputs[STAGE2_CHAPTER_BLUEPRINTS]):
                ranges.setdefault(row['chapter_ordinal'], set()).update(
                    f"p:{anchor['start_para']}:{anchor['end_para']}" for anchor in row['evidence'])
    # 声明的证据章与传播目标章独立；倒叙、后见证据按实际段范围进入依赖。
    for registered in knowledge.registered_events:
        if registered.retired:
            continue
        for proof in registered.event.evidence_refs:
            ordinal = proof.chapter_ordinal
            if ordinal in selected | source_chapters:
                continue
            parts = proof.evidence_ref.split(':')
            if len(parts) == 3 and parts[0] == 'p':
                ranges.setdefault(ordinal, set()).add(proof.evidence_ref)
            else:
                chapter = next(row for row in bp if row.chapter_ordinal == ordinal)
                anchor = next(row for row in chapter.evidence_anchors if row.evidence_id == proof.evidence_ref)
                ranges.setdefault(ordinal, set()).add(f'p:{anchor.start_para}:{anchor.end_para}')
    # 人物来自本章与真正下发的依赖闭包，包含对白参与者、观察者、状态持有者和知识受众。
    # 源文中的身份判断仍交 Commander；本投影不把“提及”推断为“在场”。
    character_material = [row.model_dump(mode='json') for row in bp if row.chapter_ordinal in selected]
    character_material.extend(select_scope_input(row, snapshot.inputs[row.input_rel]) for row in result)
    character_material.append({'knowledge': [row.model_dump(mode='json') for row in knowledge.obligations],
                               'events': [row.model_dump(mode='json') for row in knowledge.registered_events]})
    character_ids = character_references(character_material)
    # 名册称呼中的 by 也是需要解释的正式人物引用；只扩展被消费人物的依赖。
    while True:
        people = character_subjects(snapshot.inputs[STAGE2_CHARACTERS], sorted(character_ids))
        expanded = character_ids | character_references(people)
        if expanded == character_ids:
            break
        character_ids = expanded
    result.append(ScopeSelection(input_rel=STAGE2_CHARACTERS, projection='plot_characters',
        chapter_ordinal=min(selected), selected_ids=tuple(sorted(character_ids))))
    for row in people:
        for anchor in row['evidence']:
            ordinal = anchor['chapter_ordinal']
            if ordinal not in selected | source_chapters:
                start, end = paragraph_range_of(texts[ordinal], anchor['start'], anchor['end'])
                ranges.setdefault(ordinal, set()).add(f'p:{start}:{end}')
    result.extend(ScopeSelection(input_rel=STAGE1_MAIN_TEXT, projection='source_chapter', chapter_ordinal=o)
                  for o in sorted(source_chapters))
    result.extend(ScopeSelection(input_rel=STAGE1_MAIN_TEXT, projection='source_ranges',
                  chapter_ordinal=o, selected_ids=tuple(sorted(refs))) for o, refs in sorted(ranges.items()))
    return tuple(result)
